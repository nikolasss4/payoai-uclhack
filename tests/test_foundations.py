import psycopg
import pytest

from payoai.db import new_id, tx
from payoai.domain.money import canonical_payload, format_minor, payload_hash, to_minor
from payoai.providers.base import SubmitRequest
from payoai.providers.simulator import SimulatorProvider, ledger, set_balance, set_fault
from payoai.queue import claim, enqueue, finish
from payoai.trace import TraceContext, audit, redact, step


def test_money_is_exact():
    assert to_minor("1,250.00", "GBP") == 125000
    assert to_minor("300", "USDC") == 300000000
    assert format_minor(125000, "GBP") == "£1,250.00"
    with pytest.raises(TypeError):
        to_minor(1.5, "GBP")
    with pytest.raises(ValueError):
        to_minor("1.234", "GBP")


def test_any_material_change_changes_the_hash():
    base = dict(
        team_id="t", source_account_id="a", destination_id="d", destination_details={"iban": "GB1"},
        rail="FIAT_BANK", provider="P", amount_minor=100, asset="GBP", max_fee_minor=1,
        obligation_id="o", payment_reference="r",
    )
    original = payload_hash(canonical_payload(**base))
    assert original == payload_hash(canonical_payload(**base))
    for key, value in [("amount_minor", 101), ("destination_id", "x"), ("asset", "USD"), ("max_fee_minor", 2), ("destination_details", {"iban": "GB2"})]:
        assert payload_hash(canonical_payload(**{**base, key: value})) != original


def test_redaction_removes_secrets_and_full_details():
    out = redact({"api_key": "abc", "iban": "GB00EXAM00000000000009", "note": "pay GB00EXAM00000000000009 or 0x1234567890123456789012345678901234567890", "n": 1})
    assert out["api_key"] == "[redacted]"
    assert out["iban"] == "[redacted]"
    assert "GB00EXAM00000000000009" not in out["note"]
    assert "1234567890123456789012345678901234567890" not in out["note"]


def test_audit_events_cannot_be_rewritten(team):
    with tx() as conn:
        audit(conn, team["id"], "SYSTEM", None, "Test", "team", team["id"])
    for sql in ("UPDATE audit_events SET event_type = 'x' WHERE team_id = %s", "DELETE FROM audit_events WHERE team_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            with tx() as conn:
                conn.execute(sql, (team["id"],))


def test_trace_step_records_success_and_failure(team):
    run_id = new_id("run")
    with tx() as conn:
        conn.execute(
            "INSERT INTO routine_runs (id, team_id, routine, trigger_type, status) VALUES (%s, %s, 'T', 'SYSTEM', 'RUNNING')",
            (run_id, team["id"]),
        )
    ctx = TraceContext(team["id"], run_id=run_id)
    with step(ctx, "NODE", "ok", input={"api_key": "s3cret"}) as s:
        s.output = {"a": 1}
    with pytest.raises(ValueError):
        with step(ctx, "NODE", "bad"):
            raise ValueError("boom")
    with tx() as conn:
        rows = conn.execute("SELECT name, status, input, error FROM trace_steps WHERE run_id = %s ORDER BY started_at", (run_id,)).fetchall()
    assert [(r["name"], r["status"]) for r in rows] == [("ok", "COMPLETED"), ("bad", "FAILED")]
    assert rows[0]["input"]["api_key"] == "[redacted]"
    assert "boom" in rows[1]["error"]


def test_queue_claims_once_and_dedupes(team):
    key = new_id("key")
    with tx() as conn:
        assert enqueue(conn, "test_kind", team["id"], {"x": 1}, dedupe_key=key)
        assert not enqueue(conn, "test_kind", team["id"], {"x": 2}, dedupe_key=key)
    job = claim("w1", ["test_kind"])
    assert job["payload"] == {"x": 1}
    assert claim("w2", ["test_kind"]) is None
    finish(job["id"])


def test_one_running_step_per_chat(team):
    chat_id = new_id("chat")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat_id, team["id"], team["users"]["OPERATOR"]))
        conn.execute("INSERT INTO routine_runs (id, team_id, routine, chat_id, trigger_type, status) VALUES (%s, %s, 'A', %s, 'CHAT', 'RUNNING')", (new_id("run"), team["id"], chat_id))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with tx() as conn:
            conn.execute("INSERT INTO routine_runs (id, team_id, routine, chat_id, trigger_type, status) VALUES (%s, %s, 'B', %s, 'CHAT', 'RUNNING')", (new_id("run"), team["id"], chat_id))


def _request(reference, key="k1"):
    return SubmitRequest(new_id(key), "acct", 100, "GBP", {"iban": "GB1"}, reference)


def test_simulator_timeout_after_accept_really_paid():
    provider, reference = SimulatorProvider("SIM_T"), new_id("ref")
    set_balance("acct", 1000)
    set_fault(reference, submit="timeout_after_accept")
    request = _request(reference)
    assert provider.submit(request).outcome == "UNKNOWN"
    assert len(ledger(reference)) == 1
    assert provider.get_status(None, reference, request.idempotency_key).status == "COMPLETED"


def test_simulator_timeout_before_accept_did_not_pay():
    provider, reference = SimulatorProvider("SIM_T"), new_id("ref")
    set_fault(reference, submit="timeout_before_accept")
    request = _request(reference)
    assert provider.submit(request).outcome == "UNKNOWN"
    assert ledger(reference) == []
    assert provider.get_status(None, reference, request.idempotency_key).status == "NOT_FOUND"


def test_simulator_same_key_pays_once():
    provider, reference = SimulatorProvider("SIM_T"), new_id("ref")
    set_balance("acct", 1000)
    request = _request(reference)
    assert provider.submit(request).external_id == provider.submit(request).external_id
    assert len(ledger(reference)) == 1

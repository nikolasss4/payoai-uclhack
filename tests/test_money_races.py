"""Real races on two threads, and the money bugs found in review. Nothing here calls a real model."""

import json
import threading
from decimal import Decimal

import pytest

from payoai.agent import chips, llm, read_tools
from payoai.compliance.rules import uk_vat
from payoai.db import fetch_all, fetch_one, tx
from payoai.domain.money import to_minor
from payoai.fixtures import invoice_fields, render_invoice, seed_business, with_quotes
from payoai.money import approval, execute, expense, reconcile
from payoai.providers.simulator import ledger, set_balance, set_fault, sign_webhook
from payoai.routines import accounting, engine, inputs as routine_inputs
from payoai.routines.nodes import decision
from payoai.routines.nodes.documents import store_document
from payoai.routines.nodes.extra import recheck_selection


@pytest.fixture()
def biz(team):
    return {"team": team, **seed_business(team["id"], team["users"]["APPROVER"])}


@pytest.fixture(autouse=True)
def fake_model():
    state = {"fields": None, "calls": [], "extract_error": None}

    def fake(step_name, parts, schema):
        state["calls"].append(step_name)
        if step_name == "extract_fields":
            return state["extract_error"] or state["fields"]
        if step_name == "investigate":
            return {"findings": []}
        if step_name == "draft_request":
            return {"message": "Please send the receipt."}
        return {"explanation": "Plain explanation."}

    llm.set_fake(fake)
    yield state
    llm.set_fake(None)


def pay_bill(biz, fake_model, fields, routine="pay_bill"):
    fake_model["fields"] = with_quotes(fields)
    team = biz["team"]
    document_id = store_document(team["id"], "invoice.txt", "text/plain", render_invoice(fields).encode(), team["users"]["OPERATOR"])
    return engine.start(routine, team["id"], {"document_id": document_id}, "CHAT", actor=team["users"]["OPERATOR"])


def card(result):
    with tx() as conn:
        return fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (result["approval_id"],))


def second_approver(team):
    user_id = team["id"] + "_p"
    with tx() as conn:
        conn.execute("INSERT INTO users (id, name, email) VALUES (%s, 'Priya', %s)", (user_id, user_id + "@example.test"))
        conn.execute("INSERT INTO memberships VALUES (%s, %s, 'APPROVER')", (team["id"], user_id))
    return user_id


def race(*fns):
    """Starts both at once on separate threads and returns what each did, or the exception."""
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def run(index, fn):
        barrier.wait(timeout=30)
        try:
            results[index] = fn()
        except Exception as exc:  # noqa: BLE001
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert all(not t.is_alive() for t in threads), "a thread is stuck, which suggests a deadlock"
    return results


def execute_jobs(team_id, approval_id):
    with tx() as conn:
        return fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE kind = 'execute_payment' AND team_id = %s AND payload->>'approval_id' = %s", (team_id, approval_id))["n"]


@pytest.mark.parametrize("round_number", range(3))
def test_two_approvers_one_winner_one_job(biz, fake_model, round_number):
    team = biz["team"]
    other = second_approver(team)
    result = pay_bill(biz, fake_model, invoice_fields(f"R-A{round_number}"))
    a = card(result)
    first, second = race(
        lambda: approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED"),
        lambda: approval.decide(team["id"], other, a["id"], a["payload_hash"], "APPROVED"),
    )
    assert not isinstance(first, Exception) and not isinstance(second, Exception), (first, second)
    assert [first.won, second.won].count(True) == 1, (first, second)
    loser = first if not first.won else second
    assert "Already approved" in loser.message
    assert execute_jobs(team["id"], a["id"]) == 1
    with tx() as conn:
        grants = fetch_one(conn, "SELECT count(*) AS n FROM audit_events WHERE team_id = %s AND event_type = 'ApprovalGranted' AND entity_id = %s", (team["id"], result["intent_id"]))["n"]
    assert grants == 1


def test_approve_against_reject_has_one_winner(biz, fake_model):
    team = biz["team"]
    other = second_approver(team)
    result = pay_bill(biz, fake_model, invoice_fields("R-R"))
    a = card(result)
    first, second = race(
        lambda: approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED"),
        lambda: approval.decide(team["id"], other, a["id"], a["payload_hash"], "REJECTED", reason="No"),
    )
    assert [first.won, second.won].count(True) == 1, (first, second)
    expected_jobs = 1 if first.won else 0
    assert execute_jobs(team["id"], a["id"]) == expected_jobs
    assert card(result)["status"] == ("APPROVED" if first.won else "REJECTED")


@pytest.mark.parametrize("round_number", range(3))
def test_approve_against_replace_bill_has_one_outcome_and_nothing_sent_twice(biz, fake_model, round_number):
    team = biz["team"]
    number = f"R-B{round_number}"
    result = pay_bill(biz, fake_model, invoice_fields(number))
    a = card(result)
    fake_model["fields"] = with_quotes(invoice_fields(number, total="480.00"))
    approve, replaced = race(
        lambda: approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED"),
        lambda: engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": result["obligation_id"], "reason": "Correction"}), "CONFIRMATION", actor=team["users"]["OPERATOR"]),
    )
    assert not isinstance(approve, Exception) and not isinstance(replaced, Exception), (approve, replaced)
    jobs = execute_jobs(team["id"], a["id"])
    assert jobs <= 1
    assert jobs == (1 if approve.won else 0)
    final = card(result)
    with tx() as conn:
        old_bill = fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"]
    # Replace Bill may also cancel an approved payment that has no attempt yet. See routines.md.
    assert (final["status"] == "CANCELLED") == (old_bill == "SUPERSEDED")
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    if final["status"] == "CANCELLED":
        assert outcome["status"] == "STOPPED"
        with tx() as conn:
            # A stopped payment must not bring a Superseded Bill back to life.
            assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"] == "SUPERSEDED"
    assert len(ledger(number)) <= 1
    with tx() as conn:
        live = fetch_all(conn, "SELECT id FROM payment_intents WHERE team_id = %s AND status NOT IN ('FAILED', 'REJECTED', 'CANCELLED')", (team["id"],))
    assert len(live) == 1


def test_execute_against_replace_bill_never_sends_for_a_superseded_bill(biz, fake_model):
    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("R-X"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    fake_model["fields"] = with_quotes(invoice_fields("R-X"))
    sent, replaced = race(
        lambda: execute.execute(team["id"], result["intent_id"], a["id"]),
        lambda: engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": result["obligation_id"], "reason": "Correction"}), "CONFIRMATION", actor=team["users"]["OPERATOR"]),
    )
    assert not isinstance(sent, Exception) and not isinstance(replaced, Exception), (sent, replaced)
    with tx() as conn:
        status = fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"]
    # Either the attempt came first and the bill stays, or the bill was replaced and nothing left.
    if status == "SUPERSEDED":
        assert sent["status"] == "STOPPED"
        assert all(t["idempotency_key"] != f"{team['id']}-{result['obligation_id']}-v1" for t in ledger("R-X"))
    else:
        assert sent["status"] == "SUBMITTED" and "stop" in replaced and replaced["stop"]


def test_two_execute_jobs_send_once(biz, fake_model):
    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("R-E"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    first, second = race(lambda: execute.execute(team["id"], result["intent_id"], a["id"]), lambda: execute.execute(team["id"], result["intent_id"], a["id"]))
    assert not isinstance(first, Exception) and not isinstance(second, Exception), (first, second)
    assert len(ledger("R-E")) == 1
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM payment_attempts WHERE intent_id = %s", (result["intent_id"],))["n"] == 1


def test_two_reconciliations_make_one_expense(biz, fake_model):
    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("R-C"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    first, second = race(lambda: reconcile.reconcile(outcome["attempt_id"]), lambda: reconcile.reconcile(outcome["attempt_id"]))
    assert not isinstance(first, Exception) and not isinstance(second, Exception), (first, second)
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM expense_versions WHERE intent_id = %s", (result["intent_id"],))["n"] == 1


def test_pause_after_the_checks_still_stops_the_send(biz, fake_model):
    from payoai.trace import TraceContext

    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("R-P"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    with tx() as conn:
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (result["intent_id"],))
        conn.execute("UPDATE team_controls SET payments_paused = true WHERE team_id = %s", (team["id"],))
    with pytest.raises(execute.NotApprovedAnyMore):
        execute._record_attempt(TraceContext(team["id"], run_id=result["run_id"]), intent, a["id"])
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM payment_attempts WHERE intent_id = %s", (result["intent_id"],))["n"] == 0


# Late confirmations


def _failed_payment(biz, fake_model, number):
    team = biz["team"]
    set_fault(number, final="FAILED", pending_polls=0)
    result = pay_bill(biz, fake_model, invoice_fields(number))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    assert reconcile.reconcile(outcome["attempt_id"])["state"] == "FAILED"
    return result, outcome["attempt_id"]


def _provider_now_says_completed(attempt_id):
    with tx() as conn:
        conn.execute("UPDATE sim_transfers SET status = 'COMPLETED' WHERE idempotency_key = (SELECT idempotency_key FROM payment_attempts WHERE id = %s)", (attempt_id,))


def _notification(team_id, kind):
    with tx() as conn:
        return fetch_all(conn, "SELECT payload FROM notifications WHERE team_id = %s AND kind = %s", (team_id, kind))


def _webhook_reconcile(attempt_id):
    return reconcile.reconcile(attempt_id, "WEBHOOK")


def test_late_confirmation_after_failed_updates_and_raises_critical(biz, fake_model):
    team = biz["team"]
    result, attempt_id = _failed_payment(biz, fake_model, "L-1")
    _provider_now_says_completed(attempt_id)
    assert _webhook_reconcile(attempt_id)["state"] == "CONFIRMED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (result["intent_id"],))["status"] == "CONFIRMED"
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"] == "PAID"
        assert fetch_one(conn, "SELECT count(*) AS n FROM expense_versions WHERE intent_id = %s", (result["intent_id"],))["n"] == 1
    assert len(_notification(team["id"], "CRITICAL_LATE_CONFIRMATION")) == 1


def test_late_confirmation_holds_an_unsent_replacement_and_names_it(biz, fake_model):
    team = biz["team"]
    result, attempt_id = _failed_payment(biz, fake_model, "L-2")
    fake_model["fields"] = with_quotes(invoice_fields("L-2"))
    replaced = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": result["obligation_id"], "reason": "Try again"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    assert replaced["approval_id"], replaced
    _provider_now_says_completed(attempt_id)
    assert _webhook_reconcile(attempt_id)["state"] == "CONFIRMED"
    alert = _notification(team["id"], "CRITICAL_LATE_CONFIRMATION")[0]["payload"]
    assert alert["held_replacements"] == [replaced["intent_id"]] and alert["replacement_bills"] == [replaced["obligation_id"]]
    assert card(replaced)["status"] == "CANCELLED"
    a = card(replaced)
    assert not approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    with tx() as conn:
        # The old bill stays superseded, or the same invoice number would be live twice.
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"] == "SUPERSEDED"


def test_late_confirmation_when_a_replacement_already_went_reports_a_duplicate(biz, fake_model):
    team = biz["team"]
    result, attempt_id = _failed_payment(biz, fake_model, "L-3")
    fake_model["fields"] = with_quotes(invoice_fields("L-3"))
    replaced = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": result["obligation_id"], "reason": "Try again"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    a = card(replaced)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    # The earlier fault is keyed to this invoice number, so this one stays in flight.
    assert execute.execute(team["id"], replaced["intent_id"], a["id"])["status"] == "SUBMITTED"
    _provider_now_says_completed(attempt_id)
    assert _webhook_reconcile(attempt_id)["state"] == "CONFIRMED"
    alert = _notification(team["id"], "CRITICAL_LATE_CONFIRMATION")[0]["payload"]
    assert alert["possible_duplicates"][0]["intent_id"] == replaced["intent_id"]


def test_late_confirmation_with_a_new_payment_on_the_same_bill_does_not_crash(biz, fake_model):
    """The old unique index turned this into an error, which lost the confirmation."""
    team = biz["team"]
    result, attempt_id = _failed_payment(biz, fake_model, "L-4")
    again = engine.start("pay_bill", team["id"], {"obligation_id": result["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert again["approval_id"], again
    _provider_now_says_completed(attempt_id)
    assert _webhook_reconcile(attempt_id)["state"] == "CONFIRMED"
    assert card(again)["status"] == "CANCELLED"


def test_a_query_that_still_says_failed_changes_nothing(biz, fake_model):
    _, attempt_id = _failed_payment(biz, fake_model, "L-5")
    with tx() as conn:
        polls = fetch_one(conn, "SELECT count(*) AS n FROM jobs WHERE payload->>'attempt_id' = %s AND kind = 'reconcile_payment'", (attempt_id,))["n"]
    assert polls >= 1
    assert reconcile.reconcile(attempt_id, "TIMER")["state"] == "FAILED"


def test_a_stale_query_cannot_overwrite_a_confirmation(biz, fake_model):
    from payoai.providers.base import StatusResult

    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("L-6"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s", (outcome["attempt_id"],))
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (result["intent_id"],))
    assert reconcile.reconcile(outcome["attempt_id"])["state"] == "CONFIRMED"
    # A slower check that read PENDING before the confirmation now tries to save it.
    with tx() as conn:
        assert reconcile._apply(conn, attempt, intent, StatusResult("PENDING"), [], None) == "CONFIRMED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (result["intent_id"],))["status"] == "CONFIRMED"
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"] == "PAID"


def test_not_found_after_failed_does_not_reopen_the_bill(biz, fake_model):
    from payoai.providers.base import StatusResult

    result, attempt_id = _failed_payment(biz, fake_model, "L-7")
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT * FROM payment_attempts WHERE id = %s", (attempt_id,))
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (result["intent_id"],))
        assert reconcile._apply(conn, attempt, intent, StatusResult("NOT_FOUND"), [], None) == "FAILED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (result["obligation_id"],))["status"] == "FAILED"


def _in_review(biz, fake_model, number):
    team = biz["team"]
    set_fault(number, settled_amount_minor=47500)
    result = pay_bill(biz, fake_model, invoice_fields(number))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    assert reconcile.reconcile(outcome["attempt_id"])["state"] == "NEEDS_MANUAL_REVIEW"
    return result, outcome["attempt_id"]


def test_manual_paid_stays_labelled_manual_blocks_repayment_and_records_an_expense(biz, fake_model):
    team = biz["team"]
    result, attempt_id = _in_review(biz, fake_model, "M-1")
    assert reconcile.resolve_manually(team["id"], team["users"]["APPROVER"], attempt_id, "PAID", "Bank statement line")["outcome"] == "PAID"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (result["intent_id"],))["status"] == "MANUALLY_RESOLVED"
        assert fetch_one(conn, "SELECT count(*) AS n FROM expense_versions WHERE intent_id = %s", (result["intent_id"],))["n"] == 1
    again = engine.start("pay_bill", team["id"], {"obligation_id": result["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert again["stop"] and again["intent_id"] is None
    assert chips.check_reply(team["id"], f"Paid: [[payment:{result['intent_id']}]].", [{"x": 1}]) == []


def test_manual_not_paid_frees_the_bill_and_a_late_completion_is_critical(biz, fake_model):
    team = biz["team"]
    result, attempt_id = _in_review(biz, fake_model, "M-2")
    reconcile.resolve_manually(team["id"], team["users"]["APPROVER"], attempt_id, "NOT_PAID", "Provider call")
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (result["intent_id"],))["status"] == "FAILED"
        conn.execute("UPDATE sim_transfers SET amount_minor = 48000 WHERE reference = 'M-2'")
    assert reconcile.reconcile(attempt_id, "TIMER")["changed"] is False
    assert _webhook_reconcile(attempt_id)["state"] == "CONFIRMED"
    assert len(_notification(team["id"], "CRITICAL_LATE_CONFIRMATION")) == 1


def test_webhook_can_settle_a_review_case_that_only_timed_out(biz, fake_model):
    team = biz["team"]
    set_fault("M-3", submit="timeout_before_accept")
    result = pay_bill(biz, fake_model, invoice_fields("M-3"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    with tx() as conn:
        conn.execute("UPDATE payment_attempts SET state = 'NEEDS_MANUAL_REVIEW' WHERE id = %s", (outcome["attempt_id"],))
        conn.execute("UPDATE payment_intents SET status = 'NEEDS_MANUAL_REVIEW' WHERE id = %s", (result["intent_id"],))
        intent = fetch_one(conn, "SELECT idempotency_key FROM payment_intents WHERE id = %s", (result["intent_id"],))
        conn.execute(
            "INSERT INTO sim_transfers (external_id, provider, idempotency_key, account_external_id, amount_minor, asset, destination_ref, reference, status) VALUES (%s, 'SIMULATOR', %s, %s, 48000, 'GBP', %s, 'M-3', 'COMPLETED')",
            (team["id"] + "_tx", intent["idempotency_key"], biz["gbp_external"], _destination(result)),
        )
    # A timer never queries a final state again, and a webhook does.
    assert reconcile.reconcile(outcome["attempt_id"], "TIMER")["state"] == "NEEDS_MANUAL_REVIEW"
    assert _webhook_reconcile(outcome["attempt_id"])["state"] == "CONFIRMED"


def _destination(result):
    from payoai.providers.simulator import destination_ref

    with tx() as conn:
        return destination_ref(fetch_one(conn, "SELECT canonical FROM payment_intents WHERE id = %s", (result["intent_id"],))["canonical"]["destination_details"])


def test_webhooks_with_no_event_id_do_not_swallow_each_other(biz, fake_model):
    team = biz["team"]
    bodies = []
    for number in ("W-1", "W-2"):
        result = pay_bill(biz, fake_model, invoice_fields(number))
        a = card(result)
        # The second is a near-duplicate of the first, so it needs its review cleared.
        assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
        outcome = execute.execute(team["id"], result["intent_id"], a["id"])
        with tx() as conn:
            external = fetch_one(conn, "SELECT external_id FROM payment_attempts WHERE id = %s", (outcome["attempt_id"],))["external_id"]
        bodies.append(json.dumps({"external_id": external, "status": "COMPLETED"}).encode())
    for body in bodies:
        assert reconcile.handle_webhook("SIMULATOR", body, {"x-sim-signature": sign_webhook(body)}) == {"accepted": True, "duplicate": False}
    assert reconcile.handle_webhook("SIMULATOR", bodies[0], {"x-sim-signature": sign_webhook(bodies[0])})["duplicate"] is True


# Chase Missing Document's automatic start


def _paid_without_invoice(biz):
    team = biz["team"]
    result = engine.start("pay_someone", team["id"], routine_inputs.validate("pay_someone", {"counterparty_name": "Alex Chen", "amount": "300", "asset": "USDC", "purpose": "March"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    assert reconcile.reconcile(outcome["attempt_id"])["state"] == "CONFIRMED"
    with tx() as conn:
        return result, fetch_one(conn, "SELECT expense_id FROM expense_versions WHERE intent_id = %s", (result["intent_id"],))["expense_id"]


def _run_chase_job(team_id, expense_id):
    """What agent-worker does with the job that reconciliation queued."""
    with tx() as conn:
        job = fetch_one(conn, "SELECT * FROM jobs WHERE dedupe_key = %s", (f"chase:{expense_id}",))
    assert job and job["kind"] == "run_routine" and job["payload"]["routine"] == "chase_missing_document"
    p = job["payload"]
    return engine.start(p["routine"], team_id, p["inputs"], "CONFIRMATION", actor=p.get("actor"), chat_id=p.get("chat_id"), turn_id=p.get("turn_id"))


def test_missing_evidence_starts_the_chase_automatically(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    chased = _run_chase_job(team["id"], expense_id)
    assert chased["status"] == "AWAITING_DOCUMENT", chased
    assert chased["draft_message"] == "Please send the receipt."
    with tx() as conn:
        exceptions = fetch_all(conn, "SELECT status, details FROM exceptions WHERE expense_id = %s", (expense_id,))
        notes = fetch_all(conn, "SELECT id FROM notifications WHERE team_id = %s AND kind = 'MISSING_DOCUMENT'", (team["id"],))
    assert len(exceptions) == 1 and exceptions[0]["status"] == "OPEN" and exceptions[0]["details"]["draft_message"]
    assert len(notes) == 1


def test_a_repeat_chase_opens_no_second_exception(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    _run_chase_job(team["id"], expense_id)
    _run_chase_job(team["id"], expense_id)
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM exceptions WHERE expense_id = %s", (expense_id,))["n"] == 1


def test_an_invoiced_payment_starts_no_chase(biz, fake_model):
    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("C-1"))
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    outcome = execute.execute(team["id"], result["intent_id"], a["id"])
    reconcile.reconcile(outcome["attempt_id"])
    with tx() as conn:
        expense_id = fetch_one(conn, "SELECT expense_id FROM expense_versions WHERE intent_id = %s", (result["intent_id"],))["expense_id"]
        assert fetch_one(conn, "SELECT id FROM jobs WHERE dedupe_key = %s", (f"chase:{expense_id}",)) is None
    assert "no missing document" in engine.start("chase_missing_document", team["id"], {"expense_id": expense_id}, "CHAT", actor=team["users"]["OPERATOR"])["stop"]


def test_the_chase_survives_a_model_outage(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    llm.set_fake(lambda step, parts, schema: RuntimeError("down") if step == "draft_request" else {"explanation": "x"})
    chased = _run_chase_job(team["id"], expense_id)
    assert chased["status"] == "AWAITING_DOCUMENT" and chased["draft_message"] is None


def test_matching_evidence_finishes_the_waiting_chase(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    chased = _run_chase_job(team["id"], expense_id)
    fields = invoice_fields("RCPT-9", total="300.00", subtotal="300.00", vat="0.00", supplier="Alex Chen", currency="USDC", payment={})
    fake_model["fields"] = with_quotes(fields)
    document = store_document(team["id"], "r.txt", "text/plain", render_invoice(fields).encode(), None)
    assert accounting.attach_evidence(team["id"], team["users"]["OPERATOR"], expense_id, document)["status"] == "SUPPORTED"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM routine_runs WHERE id = %s", (chased["run_id"],))["status"] == "COMPLETED"
        assert fetch_one(conn, "SELECT status FROM exceptions WHERE expense_id = %s", (expense_id,))["status"] == "RESOLVED"


def test_old_document_date_keeps_it_missing_evidence(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    fields = invoice_fields("RCPT-OLD", total="300.00", subtotal="300.00", vat="0.00", supplier="Alex Chen", currency="USDC", payment={}, issue_date="2001-01-01")
    fake_model["fields"] = with_quotes(fields)
    document = store_document(team["id"], "r.txt", "text/plain", render_invoice(fields).encode(), None)
    outcome = accounting.attach_evidence(team["id"], team["users"]["OPERATOR"], expense_id, document)
    assert outcome["status"] == "MISSING_EVIDENCE" and outcome["differences"][0]["field"] == "date"


def test_late_evidence_cannot_undo_a_later_stage(biz, fake_model):
    team = biz["team"]
    _, expense_id = _paid_without_invoice(biz)
    with tx() as conn:
        accounting.add_version(conn, accounting.latest(conn, team["id"], expense_id), "SYSTEM", "Reviewed", status="REVIEWED")
    fields = invoice_fields("RCPT-L", total="300.00", subtotal="300.00", vat="0.00", supplier="Alex Chen", currency="USDC", payment={})
    fake_model["fields"] = with_quotes(fields)
    document = store_document(team["id"], "r.txt", "text/plain", render_invoice(fields).encode(), None)
    assert accounting.attach_evidence(team["id"], team["users"]["OPERATOR"], expense_id, document)["status"] == "REVIEWED"


# INVESTIGATE_OFF


@pytest.mark.parametrize("value,called", [("1", False), ("true", False), ("0", True), ("", True)])
def test_investigate_off_flag(biz, fake_model, monkeypatch, value, called):
    monkeypatch.setenv("INVESTIGATE_OFF", value)
    fake_model["calls"].clear()
    result = pay_bill(biz, fake_model, invoice_fields(f"O-{value or 'unset'}", total="500.00"))
    assert result["decision"] == "NEEDS_REVIEW"
    assert ("investigate" in fake_model["calls"]) is called
    assert "explain" in fake_model["calls"]


def test_investigate_off_gives_the_same_decisions_and_payment(biz, fake_model, monkeypatch):
    monkeypatch.setenv("INVESTIGATE_OFF", "1")
    off = pay_bill(biz, fake_model, invoice_fields("O-A", total="500.00"))
    monkeypatch.delenv("INVESTIGATE_OFF")
    on = pay_bill(biz, fake_model, invoice_fields("O-B", total="500.00"))
    assert off["decision"] == on["decision"]
    drop = {"NEAR_DUPLICATE"}
    assert {r["rule_id"] for r in off["reasons"]} - drop == {r["rule_id"] for r in on["reasons"]} - drop
    assert bool(off["intent_id"]) == bool(on["intent_id"])


def test_investigate_off_covers_the_batch(biz, fake_model, monkeypatch):
    team = biz["team"]
    pay_bill(biz, fake_model, invoice_fields("O-C", total="500.00"), routine="register_bill")
    monkeypatch.setenv("INVESTIGATE_OFF", "1")
    fake_model["calls"].clear()
    engine.start("prepare_batch", team["id"], {}, "CHAT", actor=team["users"]["OPERATOR"])
    assert "investigate" not in fake_model["calls"]


# Prepare Batch


def test_a_blocked_bill_does_not_use_up_a_good_bills_cash(biz, fake_model):
    from payoai.fixtures import FRAUD_IBAN

    team = biz["team"]
    # Spendable is just enough for one £480 bill plus its fee, and the blocked one sorts first.
    set_balance(biz["gbp_external"], 300000 + 48000 + 1200)
    from datetime import date, timedelta

    soon, later = (date.today() + timedelta(days=1)).isoformat(), (date.today() + timedelta(days=9)).isoformat()
    pay_bill(biz, fake_model, invoice_fields("BB-1", due_date=soon, payment={"iban": FRAUD_IBAN}), routine="register_bill")
    good = pay_bill(biz, fake_model, invoice_fields("BB-2", due_date=later, total="240.00", subtotal="200.00", vat="40.00", line_items=[{"description": "Seats", "quantity": "2", "unit_price": "100.00", "net": "200.00", "vat_rate": "20", "vat": "40.00"}]), routine="register_bill")
    batch = engine.start("prepare_batch", team["id"], {}, "CHAT", actor=team["users"]["OPERATOR"])
    items = {i["obligation_id"]: i for i in batch["items"]}
    assert not any(r["rule_id"] == "BATCH_SHORTFALL" for r in items[good["obligation_id"]]["reasons"] or []), items


def test_missing_due_date_is_listed_together_and_gets_no_payment(biz, fake_model):
    team = biz["team"]
    missing = pay_bill(biz, fake_model, invoice_fields("BI-1", due_date=None), routine="register_bill")
    with tx() as conn:
        conn.execute("UPDATE obligations SET due_date = current_date WHERE id = %s", (missing["obligation_id"],))
    batch = engine.start("prepare_batch", team["id"], {}, "CHAT", actor=team["users"]["OPERATOR"])
    assert batch["status"] == "COMPLETED"
    assert any(q["obligation_id"] == missing["obligation_id"] and q["questions"] for q in batch["needs_information"]), batch
    item = next(i for i in batch["items"] if i["obligation_id"] == missing["obligation_id"])
    assert item["approval_id"] is None and item["ticked"] is False


def test_changing_the_selection_reruns_the_allocation(biz, fake_model):
    team = biz["team"]
    set_balance(biz["gbp_external"], 300000 + 48000 + 1200 + 20000)
    results = [pay_bill(biz, fake_model, invoice_fields(n, total="480.00")) for n in ("BS-1", "BS-2")]
    ids = [r["approval_id"] for r in results if r["approval_id"]]
    assert len(ids) == 2
    both = recheck_selection(team["id"], ids)
    assert len(both["fits"]) == 1 and len(both["short"]) == 1 and both["short"][0]["shortfall_minor"] > 0
    one = recheck_selection(team["id"], [both["short"][0]["approval_id"]])
    assert one["fits"] == [both["short"][0]["approval_id"]] and one["short"] == []
    # Another team's approvals, or decided ones, never count as fitting.
    assert recheck_selection("team_other", ids)["fits"] == []


# Cash Analysis triggers


def test_registering_a_bill_and_a_final_payment_queue_cash_analysis(biz, fake_model):
    team = biz["team"]
    registered = pay_bill(biz, fake_model, invoice_fields("CA-1"), routine="register_bill")
    with tx() as conn:
        assert fetch_one(conn, "SELECT kind FROM jobs WHERE dedupe_key = %s", (f"cash:bill:{registered['obligation_id']}",))["kind"] == "cash_analysis"
    paid = pay_bill(biz, fake_model, invoice_fields("CA-2"))
    a = card(paid)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    outcome = execute.execute(team["id"], paid["intent_id"], a["id"])
    with tx() as conn:
        assert fetch_one(conn, "SELECT id FROM jobs WHERE dedupe_key = %s", (f"cash:final:{outcome['attempt_id']}:CONFIRMED",)) is None
    reconcile.reconcile(outcome["attempt_id"])
    with tx() as conn:
        assert fetch_one(conn, "SELECT kind FROM jobs WHERE dedupe_key = %s", (f"cash:final:{outcome['attempt_id']}:CONFIRMED",))["kind"] == "cash_analysis"
    # A payment still pending isn't final, so nothing is queued for it.
    set_fault("CA-3", pending_polls=2)
    pending = pay_bill(biz, fake_model, invoice_fields("CA-3"))
    a = card(pending)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    third = execute.execute(team["id"], pending["intent_id"], a["id"])
    reconcile.reconcile(third["attempt_id"])
    with tx() as conn:
        assert fetch_one(conn, "SELECT id FROM jobs WHERE dedupe_key LIKE %s", (f"cash:final:{third['attempt_id']}:%",)) is None


# Money review


@pytest.mark.parametrize("bad", ["12,00", "1e3", "Infinity", "NaN", "£5", "1,2,3", ""])
def test_to_minor_refuses_ambiguous_or_non_finite_amounts(bad):
    with pytest.raises(ValueError):
        to_minor(bad, "GBP")


@pytest.mark.parametrize("good,minor", [("1,200.50", 120050), ("1200", 120000), (".5", 50), ("12,000", 1200000), (Decimal("12.50"), 1250), (7, 700)])
def test_to_minor_keeps_normal_amounts_exact(good, minor):
    assert to_minor(good, "GBP") == minor


def test_a_float_is_still_refused():
    with pytest.raises(TypeError):
        to_minor(1.5, "GBP")


@pytest.mark.parametrize("amount", ["0", "-300"])
def test_pay_someone_refuses_zero_and_negative(amount):
    with pytest.raises(ValueError):
        routine_inputs.validate("pay_someone", {"counterparty_name": "Alex", "amount": amount, "asset": "USDC", "purpose": "x"})


@pytest.mark.parametrize("total", ["-480.00", "0.00"])
def test_a_negative_or_zero_invoice_is_blocked(biz, fake_model, total):
    result = pay_bill(biz, fake_model, invoice_fields(f"N{total}", total=total))
    assert result["decision"] == "BLOCK" and result["intent_id"] is None
    assert any(r["rule_id"] == "AMOUNT_NOT_POSITIVE" for r in result["reasons"])


def test_a_missing_fact_cannot_be_reviewed_away(biz, fake_model):
    """NEEDS_REVIEW outranks NEEDS_INFORMATION, but an unanswered question still prepares nothing."""
    result = pay_bill(biz, fake_model, invoice_fields("MI-1", total="500.00", due_date=None))
    assert result["decision"] == "NEEDS_REVIEW"
    assert any(r["outcome"] == "NEEDS_INFORMATION" for r in result["reasons"])
    assert result["intent_id"] is None


def test_can_prepare_needs_every_field():
    good = {"decision": "ELIGIBLE_FOR_APPROVAL", "reasons": [], "destination_id": "d", "source_account_id": "s", "amount_minor": "1"}
    assert decision.can_prepare(good)
    assert not decision.can_prepare({**good, "decision": "BLOCK"})
    assert not decision.can_prepare({**good, "amount_minor": None})
    assert not decision.can_prepare({**good, "decision": "NEEDS_REVIEW", "reasons": [{"outcome": "NEEDS_INFORMATION"}]})


def test_a_prepared_bill_is_not_prepared_again(biz, fake_model):
    team = biz["team"]
    first = pay_bill(biz, fake_model, invoice_fields("PP-1"))
    again = engine.start("pay_bill", team["id"], {"obligation_id": first["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert again["status"] == "COMPLETED" and "already has a payment" in again["stop"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (first["obligation_id"],))["status"] == "PAYMENT_PREPARED"


def test_an_expired_approval_does_not_hold_the_bill_forever(biz, fake_model):
    team = biz["team"]
    first = pay_bill(biz, fake_model, invoice_fields("PP-2"))
    with tx() as conn:
        conn.execute("UPDATE approvals SET expires_at = now() - interval '1 hour' WHERE id = %s", (first["approval_id"],))
    again = engine.start("pay_bill", team["id"], {"obligation_id": first["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert again["approval_id"] and again["approval_id"] != first["approval_id"], again
    assert card(first)["status"] == "CANCELLED"


def test_reupload_after_a_failed_payment_is_a_clear_block_not_a_crash(biz, fake_model):
    _failed_payment(biz, fake_model, "F-R")
    again = pay_bill(biz, fake_model, invoice_fields("F-R"))
    assert again["status"] == "COMPLETED", again
    assert again["decision"] == "BLOCK" and any(r["rule_id"] == "DUPLICATE_INVOICE" and "Replace Bill" in r["reason"] for r in again["reasons"])


def test_replacing_a_failed_bill_with_a_new_payment_works(biz, fake_model):
    team = biz["team"]
    result, _ = _failed_payment(biz, fake_model, "F-P")
    fake_model["fields"] = with_quotes(invoice_fields("F-P"))
    replaced = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": result["obligation_id"], "reason": "Retry"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    assert replaced["approval_id"], replaced


def test_a_replacement_that_cannot_be_read_restores_the_old_bill(biz, fake_model):
    team = biz["team"]
    first = pay_bill(biz, fake_model, invoice_fields("RU-1"))
    fake_model["extract_error"] = RuntimeError("model down")
    outcome = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": first["obligation_id"], "reason": "Fix"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    assert outcome["stop"] and outcome["restored_bill"] == first["obligation_id"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM obligations WHERE id = %s", (first["obligation_id"],))["status"] == "NEEDS_REVIEW"
    assert card(first)["status"] == "CANCELLED"
    fake_model["extract_error"] = None
    again = engine.start("pay_bill", team["id"], {"obligation_id": first["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert again["approval_id"]


def test_replacing_a_bill_with_no_document_changes_nothing(biz):
    team = biz["team"]
    someone = engine.start("pay_someone", team["id"], routine_inputs.validate("pay_someone", {"counterparty_name": "Alex Chen", "amount": "300", "asset": "USDC", "purpose": "x"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    outcome = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": someone["obligation_id"], "reason": "Fix"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    assert "no document" in outcome["stop"]
    assert card(someone)["status"] == "PENDING"


def test_journal_always_balances_to_what_left_the_account():
    lines = expense._journal(48000, 40000, 8000, "GBP")
    assert sum(x["debit_minor"] for x in lines) == sum(x["credit_minor"] for x in lines) == 48000
    # The invoice's figures don't add up, but the bank line is still the real payment.
    lines = expense._journal(50000, 40000, 8000, "GBP")
    assert sum(x["debit_minor"] for x in lines) == sum(x["credit_minor"] for x in lines) == 50000
    assert next(x for x in lines if x["account"] == "Bank or wallet")["credit_minor"] == 50000


def test_a_reviewed_reserve_shortfall_can_execute_but_cannot_overdraw(biz, fake_model):
    team = biz["team"]
    set_balance(biz["gbp_external"], 330000)
    result = pay_bill(biz, fake_model, invoice_fields("RS-1"))
    assert any(r["rule_id"] == "CASH_BELOW_RESERVE" for r in result["reasons"])
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won
    assert execute.execute(team["id"], result["intent_id"], a["id"])["status"] == "SUBMITTED"
    # Counterexample: the money itself isn't there any more.
    set_balance(biz["gbp_external"], 330000)
    second = pay_bill(biz, fake_model, invoice_fields("RS-2"))
    b = card(second)
    assert approval.decide(team["id"], team["users"]["APPROVER"], b["id"], b["payload_hash"], "APPROVED", reviewed=True).won
    set_balance(biz["gbp_external"], 40000)
    assert execute.execute(team["id"], second["intent_id"], b["id"])["status"] == "STOPPED"
    assert ledger("RS-2") == []


def test_cash_that_drops_into_the_reserve_without_review_is_stopped(biz, fake_model):
    team = biz["team"]
    result = pay_bill(biz, fake_model, invoice_fields("RS-3"))
    assert result["decision"] == "ELIGIBLE_FOR_APPROVAL"
    a = card(result)
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    set_balance(biz["gbp_external"], 330000)
    assert execute.execute(team["id"], result["intent_id"], a["id"])["status"] == "STOPPED"


# VAT arithmetic


def _vat_fields(net, vat, rate="20"):
    return {"currency": "GBP", "subtotal": None, "total": None, "line_items": [{"description": "x", "net": net, "vat": vat, "vat_rate": rate}]}


def test_vat_line_is_exact_where_a_float_would_lose_the_pennies():
    # net * 20 / 100 is above what a float holds exactly, so a float version reports a false difference.
    net, vat = "9007199254740993.01", "1801439850948198.60"
    assert round(to_minor(net, "GBP") * 20.0 / 100) != to_minor(vat, "GBP")
    assert uk_vat._arithmetic(_vat_fields(net, vat), "GBP") == []


def test_vat_line_rule_still_catches_a_wrong_figure():
    found = uk_vat._arithmetic(_vat_fields("100.00", "19.97"), "GBP")
    assert found and found[0]["rule_id"] == "UK-ARITH-VAT-LINE"
    assert uk_vat._arithmetic(_vat_fields("100.00", "19.99"), "GBP") == []  # Within one penny.


def test_vat_rate_with_a_decimal_is_exact():
    assert uk_vat._arithmetic(_vat_fields("100.00", "17.50", "17.5%"), "GBP") == []


@pytest.mark.parametrize("rate", ["abc", "NaN", "Infinity", "", None])
def test_an_unreadable_vat_rate_is_skipped_and_never_crashes(rate):
    assert uk_vat._arithmetic(_vat_fields("100.00", "20.00", rate), "GBP") == []


def test_the_vat_rules_use_no_float():
    from pathlib import Path

    source = (Path(uk_vat.__file__)).read_text()
    assert "float(" not in source


# What the model sees


def test_investigate_sees_formatted_money_and_no_minor_units(biz, fake_model):
    seen = []
    original = llm._fake

    def spy(step_name, parts, schema):
        if step_name == "investigate":
            seen.append(str(parts))
        return original(step_name, parts, schema)

    pay_bill(biz, fake_model, invoice_fields("MU-1"), routine="register_bill")
    llm.set_fake(spy)
    pay_bill(biz, fake_model, invoice_fields("MU-2", total="500.00"))
    assert seen, "Investigate should have run on this review"
    assert "£480.00" in seen[0]
    assert "48000" not in seen[0] and "amount_minor" not in seen[0] and "minor units" not in seen[0]


def test_compliance_reasons_show_money_as_people_read_it():
    found = uk_vat.evaluate(invoice_fields("MU-3", total="500.00"))
    reason = next(r["reason"] for r in found if r["rule_id"] == "UK-ARITH-TOTAL")
    assert "£480.00" in reason and "£500.00" in reason and "minor units" not in reason


def test_manual_resolution_leaves_no_payment_showing_review(biz, fake_model):
    team = biz["team"]
    paid, paid_attempt = _in_review(biz, fake_model, "MR-1")
    reconcile.resolve_manually(team["id"], team["users"]["APPROVER"], paid_attempt, "PAID", "Statement")
    assert read_tools.get_payment(team["id"], paid["intent_id"])["status"] == "MANUALLY_RESOLVED"
    unpaid, unpaid_attempt = _in_review(biz, fake_model, "MR-2")
    reconcile.resolve_manually(team["id"], team["users"]["APPROVER"], unpaid_attempt, "NOT_PAID", "Provider call")
    assert read_tools.get_payment(team["id"], unpaid["intent_id"])["status"] == "FAILED"
    assert read_tools.list_payments(team["id"], "NEEDS_MANUAL_REVIEW") == []
    # Counterexample: refused without evidence, and the payment still shows review.
    third, third_attempt = _in_review(biz, fake_model, "MR-3")
    with pytest.raises(ValueError):
        reconcile.resolve_manually(team["id"], team["users"]["APPROVER"], third_attempt, "PAID", "  ")
    assert read_tools.get_payment(team["id"], third["intent_id"])["status"] == "NEEDS_MANUAL_REVIEW"


# Read Tools and details


def test_get_run_does_not_return_full_account_details(biz):
    from payoai.fixtures import FRAUD_IBAN

    team = biz["team"]
    run = engine.start("add_counterparty", team["id"], routine_inputs.validate("add_counterparty", {"display_name": "New Co", "iban": FRAUD_IBAN, "account_number": "00000009", "sort_code": "000000"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    text = str(read_tools.get_run(team["id"], run["run_id"]))
    assert FRAUD_IBAN not in text and "00000009" not in text


def test_read_tool_for_a_payment_shows_the_note(biz, fake_model):
    result = pay_bill(biz, fake_model, invoice_fields("RT-1"))
    assert read_tools.get_payment(biz["team"]["id"], result["intent_id"])["notes"] == []

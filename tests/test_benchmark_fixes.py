"""Bugs the benchmark found, each with a counterexample."""

import threading
import time

import pytest

from payoai.agent import llm, main_loop
from payoai.compliance.rules import uk_vat
from payoai.db import fetch_one, new_id, tx
from payoai.fixtures import FRAUD_IBAN, invoice_fields, render_invoice, seed_business, with_quotes
from payoai.money import approval, execute
from payoai.providers import get_provider
from payoai.providers.base import SubmitResult
from payoai.providers.simulator import ledger
from payoai.routines import engine
from payoai.routines.nodes.documents import store_document


@pytest.fixture()
def biz(team):
    return {"team": team, **seed_business(team["id"], team["users"]["APPROVER"])}


@pytest.fixture(autouse=True)
def fake_model():
    state = {"fields": None, "calls": []}

    def fake(step_name, parts, schema):
        state["calls"].append(step_name)
        if step_name == "extract_fields":
            return state["fields"]
        if step_name == "investigate":
            return {"findings": []}
        return {"explanation": "Plain explanation."}

    llm.set_fake(fake)
    yield state
    llm.set_fake(None)
    main_loop.set_fake_model(None)


def bill(biz, fake_model, fields, routine="pay_bill"):
    fake_model["fields"] = with_quotes(fields)
    team = biz["team"]
    document_id = store_document(team["id"], "i.txt", "text/plain", render_invoice(fields).encode(), team["users"]["OPERATOR"])
    return engine.start(routine, team["id"], {"document_id": document_id}, "CHAT", actor=team["users"]["OPERATOR"])


def approved(biz, fake_model, number):
    team = biz["team"]
    result = bill(biz, fake_model, invoice_fields(number))
    with tx() as conn:
        a = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (result["approval_id"],))
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    return result


def attempt_state(intent_id):
    with tx() as conn:
        return fetch_one(conn, "SELECT state FROM payment_attempts WHERE intent_id = %s", (intent_id,))["state"]


# 1. Overlapping Execute Payment runs


@pytest.mark.parametrize("outcome,final", [("ACCEPTED", "SUBMITTED"), ("REJECTED", "REJECTED")])
def test_a_second_run_never_overwrites_the_first_runs_result(biz, fake_model, monkeypatch, outcome, final):
    team = biz["team"]
    result = approved(biz, fake_model, f"OV-{outcome}")
    provider = get_provider("SIMULATOR")
    real = provider.submit
    started = threading.Event()

    def slow(request):
        started.set()
        time.sleep(1.5)  # The repeat job runs while this one is still sending.
        return real(request) if outcome == "ACCEPTED" else SubmitResult("REJECTED", reason="invalid details")

    monkeypatch.setattr(provider, "submit", slow)
    out = {}
    first = threading.Thread(target=lambda: out.update(first=execute.execute(team["id"], result["intent_id"], result["approval_id"])))
    first.start()
    assert started.wait(30)
    out["second"] = execute.execute(team["id"], result["intent_id"], result["approval_id"])
    first.join(60)
    assert out["second"]["status"] == "IN_PROGRESS"
    assert out["first"]["status"] == final
    assert attempt_state(result["intent_id"]) == final
    assert len(ledger(f"OV-{outcome}")) == (1 if outcome == "ACCEPTED" else 0)


def test_a_late_result_cannot_overwrite_unknown_from_crash_recovery(biz, fake_model):
    from payoai.trace import TraceContext

    team = biz["team"]
    result = approved(biz, fake_model, "OV-LATE")
    with tx() as conn:
        intent = fetch_one(conn, "SELECT * FROM payment_intents WHERE id = %s", (result["intent_id"],))
    ctx = TraceContext(team["id"], run_id=result["run_id"])
    attempt = execute._record_attempt(ctx, intent, result["approval_id"])
    with tx() as conn:
        conn.execute("UPDATE payment_attempts SET created_at = now() - interval '11 minutes' WHERE id = %s", (attempt["id"],))
    assert execute.execute(team["id"], result["intent_id"], result["approval_id"])["status"] == "UNKNOWN"
    late = execute._record_result(ctx, intent, attempt, SubmitResult("REJECTED", reason="late"))
    assert late["status"] == "UNKNOWN" and attempt_state(result["intent_id"]) == "UNKNOWN"
    with tx() as conn:
        assert fetch_one(conn, "SELECT status FROM payment_intents WHERE id = %s", (result["intent_id"],))["status"] == "UNKNOWN"


def test_a_recorded_result_is_left_alone_by_a_repeat_job(biz, fake_model):
    team = biz["team"]
    result = approved(biz, fake_model, "OV-DONE")
    assert execute.execute(team["id"], result["intent_id"], result["approval_id"])["status"] == "SUBMITTED"
    assert execute.execute(team["id"], result["intent_id"], result["approval_id"])["status"] == "SUBMITTED"
    assert len(ledger("OV-DONE")) == 1


# 2. Check Bill Compliance on a registered bill


def test_compliance_check_on_a_registered_bill_uses_its_document_and_extraction(biz, fake_model):
    team = biz["team"]
    registered = bill(biz, fake_model, invoice_fields("CC-1", total="500.00"), routine="register_bill")
    fake_model["calls"].clear()
    report = engine.start("check_bill_compliance", team["id"], {"obligation_id": registered["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])
    assert report["status"] == "COMPLETED" and not report.get("stop"), report
    assert any(r["rule_id"] == "UK-ARITH-TOTAL" for r in report["report"])
    assert "extract_fields" not in fake_model["calls"]


def test_compliance_check_on_a_bill_with_no_document_or_another_team_stops(biz, fake_model):
    from payoai.routines import inputs as routine_inputs

    team = biz["team"]
    someone = engine.start("pay_someone", team["id"], routine_inputs.validate("pay_someone", {"counterparty_name": "Alex Chen", "amount": "300", "asset": "USDC", "purpose": "x"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    assert "no document" in engine.start("check_bill_compliance", team["id"], {"obligation_id": someone["obligation_id"]}, "CHAT", actor=team["users"]["OPERATOR"])["stop"]
    registered = bill(biz, fake_model, invoice_fields("CC-2"), routine="register_bill")
    other = new_id("team")
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Other')", (other,))
    assert "wasn't found in this team" in engine.start("check_bill_compliance", other, {"obligation_id": registered["obligation_id"]}, "SYSTEM")["stop"]


# 3. New bank details in a payment request


class Script:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.results = []

    def __call__(self, model_input, session):
        self.results.append(model_input)
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            return main_loop.ModelTurn(reply, [], "i", "completed")
        name, args = reply
        return main_loop.ModelTurn("", [{"id": new_id("c"), "name": name, "arguments": args}], "i", "requires_action")


def turn(biz, text):
    team = biz["team"]
    chat_id, turn_id = new_id("chat"), new_id("turn")
    with tx() as conn:
        conn.execute("INSERT INTO chats (id, team_id, user_id) VALUES (%s, %s, %s)", (chat_id, team["id"], team["users"]["OPERATOR"]))
        conn.execute("INSERT INTO turns (id, chat_id, team_id, seq, user_input) VALUES (%s, %s, %s, 0, %s)", (turn_id, chat_id, team["id"], text))
    return team["id"], team["users"]["OPERATOR"], chat_id, turn_id


def test_new_details_in_the_request_start_no_payment_to_the_destination_on_file(biz):
    pay = ("start_routine", {"routine": "pay_someone", "inputs": {"counterparty_name": "Examplo", "amount": "480", "asset": "GBP", "purpose": "Seats"}})
    script = Script(pay, "I can't use those details yet.")
    main_loop.set_fake_model(script)
    main_loop.run_turn(*turn(biz, f"Pay Examplo £480 for seats, their new account is {FRAUD_IBAN}"))
    result = script.results[1][0]["result"]
    assert result["status"] == "REFUSED" and "Add Counterparty" in result["reason"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM start_confirmations WHERE team_id = %s", (biz["team"]["id"],))["n"] == 0


def test_the_same_request_without_new_details_still_shows_a_card(biz):
    pay = ("start_routine", {"routine": "pay_someone", "inputs": {"counterparty_name": "Examplo", "amount": "480", "asset": "GBP", "purpose": "Seats"}})
    script = Script(pay, "Tap the card to start.")
    main_loop.set_fake_model(script)
    main_loop.run_turn(*turn(biz, "Pay Examplo £480 for seats"))
    assert script.results[1][0]["result"]["status"] == "CONFIRMATION_REQUIRED"


def test_the_prompt_states_the_rule():
    assert "don't start a payment Routine at all" in main_loop.SYSTEM and "Add Counterparty" in main_loop.SYSTEM


# 4. Supplier country


def test_missing_country_with_a_uk_address_is_checked_as_uk():
    fields = invoice_fields("UC-1", supplier_country=None)
    assert uk_vat.classify(fields) == "FULL_VAT"
    assert not any(r["rule_id"] == "UK-UNCLEAR-COUNTRY" for r in uk_vat.evaluate(fields))


@pytest.mark.parametrize("address", ["12 Rue de Rivoli, 75001 Paris", "", None, "Somewhere on London Road"])
def test_missing_country_without_a_clear_uk_address_needs_review(address):
    fields = invoice_fields("UC-2", supplier_country=None, supplier_address=address)
    assert uk_vat.classify(fields) == "UNCLEAR"
    found = uk_vat.evaluate(fields)
    assert any(r["rule_id"] == "UK-UNCLEAR-COUNTRY" and r["outcome"] == "NEEDS_REVIEW" for r in found)
    assert not any(r["outcome"] == "BLOCK" for r in found)


def test_named_country_still_wins():
    assert uk_vat.classify(invoice_fields("UC-3", supplier_country="GB", supplier_address=None)) == "FULL_VAT"
    assert uk_vat.classify(invoice_fields("UC-4", supplier_country="FR")) == "UNSUPPORTED"
    assert uk_vat.classify(invoice_fields("UC-5", supplier_country=None, supplier_address="1 High St, Leeds, United Kingdom")) == "FULL_VAT"


def test_missing_country_reaches_the_bill_as_review(biz, fake_model):
    result = bill(biz, fake_model, invoice_fields("UC-6", supplier_country=None, supplier_address="Unit 4, Industrial Estate"))
    assert result["decision"] == "NEEDS_REVIEW" and any(r["rule_id"] == "UK-UNCLEAR-COUNTRY" for r in result["reasons"])

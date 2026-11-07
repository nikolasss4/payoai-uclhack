"""Invoice-triggered Occurrences: one claim per period, whichever of the timer and an invoice comes first."""

import threading
from datetime import date, timedelta

import pytest

from payoai.agent import llm
from payoai.db import fetch_all, fetch_one, tx
from payoai.fixtures import ALEX_WALLET, BASE_SEPOLIA, EXAMPLO_IBAN, FRAUD_IBAN, POISONED_WALLET, SYNTHETIC_USDC, invoice_fields, render_invoice, seed_business, with_quotes
from payoai.money import approval, execute, reconcile
from payoai.routines import engine, inputs as routine_inputs, recurring
from payoai.routines.nodes.documents import store_document

PERIOD = "2099-03"


@pytest.fixture()
def biz(team):
    return {"team": team, **seed_business(team["id"], team["users"]["APPROVER"])}


@pytest.fixture(autouse=True)
def fake_model():
    state = {"fields": None}

    def fake(step_name, parts, schema):
        if step_name == "extract_fields":
            return state["fields"]
        if step_name == "investigate":
            return {"findings": []}
        if step_name == "draft_request":
            return {"message": "Please send the receipt."}
        return {"explanation": "Plain explanation."}

    llm.set_fake(fake)
    yield state
    llm.set_fake(None)


def active_mandate(biz, counterparty="Alex Chen", max_amount="300", asset="USDC", **overrides):
    team = biz["team"]
    inputs = {"counterparty_name": counterparty, "max_amount": max_amount, "asset": asset, "frequency": "MONTHLY", "day_of_period": 1, "valid_until": (date.today() + timedelta(days=200)).isoformat(), "purpose": "Monthly retainer", **overrides}
    result = engine.start("set_up_recurring", team["id"], routine_inputs.validate("set_up_recurring", inputs), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], result["mandate_id"], "ACTIVE")
    return result["mandate_id"]


def alex_invoice(number="A-100", total="300.00", period=PERIOD, payment=None, currency="USDC", **extra):
    payment = {"wallet_address": ALEX_WALLET} if payment is None else payment
    return invoice_fields(number, total=total, subtotal=total, vat="0.00", supplier="Alex Chen", currency=currency, vat_number=None, period=period, payment=payment, line_items=[{"description": "Design work", "net": total, "vat_rate": None, "vat": None}], **extra)


def send_invoice(biz, fake_model, fields, routine="pay_bill"):
    fake_model["fields"] = with_quotes(fields)
    team = biz["team"]
    document_id = store_document(team["id"], "invoice.txt", "text/plain", render_invoice(fields).encode(), team["users"]["OPERATOR"])
    return engine.start(routine, team["id"], {"document_id": document_id}, "CHAT", actor=team["users"]["OPERATOR"])


def fire(biz, mandate_id, period=PERIOD, **extra):
    return engine.start("prepare_occurrence", biz["team"]["id"], {"mandate_id": mandate_id, "period": period, **extra}, "TIMER")


def live_payments(biz):
    with tx() as conn:
        return fetch_all(conn, "SELECT * FROM payment_intents WHERE team_id = %s AND status NOT IN ('FAILED', 'REJECTED', 'CANCELLED')", (biz["team"]["id"],))


def occurrences(mandate_id):
    with tx() as conn:
        return fetch_all(conn, "SELECT * FROM occurrences WHERE mandate_id = %s", (mandate_id,))


def pay_through(biz, result, reviewed=True):
    team = biz["team"]
    with tx() as conn:
        a = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (result["approval_id"],))
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=reviewed).won
    execute.execute(team["id"], result["intent_id"], result["approval_id"])
    with tx() as conn:
        attempt = fetch_one(conn, "SELECT id FROM payment_attempts WHERE intent_id = %s", (result["intent_id"],))
    return reconcile.reconcile(attempt["id"])


def test_invoice_claims_the_period_and_the_timer_then_stops(biz, fake_model):
    mandate_id = active_mandate(biz)
    invoice = send_invoice(biz, fake_model, alex_invoice())
    assert invoice["status"] == "AWAITING_APPROVAL", invoice
    claim = occurrences(mandate_id)
    assert len(claim) == 1 and claim[0]["claimed_by"] == "INVOICE" and claim[0]["obligation_id"] == invoice["obligation_id"]
    with tx() as conn:
        assert fetch_one(conn, "SELECT mandate_id FROM obligations WHERE id = %s", (invoice["obligation_id"],))["mandate_id"] == mandate_id
    timer = fire(biz, mandate_id)
    assert "already claimed" in timer["stop"] and timer["intent_id"] is None
    assert len(live_payments(biz)) == 1


def test_timer_claimed_first_so_the_invoice_attaches_with_no_second_payment(biz, fake_model):
    mandate_id = active_mandate(biz, amount="300")
    timer = fire(biz, mandate_id)
    assert timer["status"] == "AWAITING_APPROVAL", timer
    with tx() as conn:
        before = fetch_one(conn, "SELECT warnings, payload_hash FROM payment_intents WHERE id = %s", (timer["intent_id"],))
    assert "No invoice attached." in before["warnings"]
    invoice = send_invoice(biz, fake_model, alex_invoice())
    assert invoice["attached"] is True and invoice["attached_to_payment"] == timer["intent_id"]
    assert invoice["intent_id"] is None and invoice["amount_differs"] is False
    assert [p["id"] for p in live_payments(biz)] == [timer["intent_id"]]
    with tx() as conn:
        after = fetch_one(conn, "SELECT warnings, payload_hash FROM payment_intents WHERE id = %s", (timer["intent_id"],))
        bill = fetch_one(conn, "SELECT document_id, invoice_reference FROM obligations WHERE id = %s", (timer["obligation_id"],))
    # What's approved can't change, and the card stops saying there's no invoice.
    assert after["payload_hash"] == before["payload_hash"]
    assert "No invoice attached." not in after["warnings"]
    assert bill["document_id"] and bill["invoice_reference"] == "A-100"


def test_invoice_for_a_different_amount_never_pays_it_and_forces_review(biz, fake_model):
    mandate_id = active_mandate(biz, amount="300")
    timer = fire(biz, mandate_id)
    invoice = send_invoice(biz, fake_model, alex_invoice(total="280.00"))
    assert invoice["attached"] is True and invoice["amount_differs"] is True and "280.00" in invoice["stop"] and "300.00" in invoice["stop"]
    assert [int(p["amount_minor"]) for p in live_payments(biz)] == [300000000]
    with tx() as conn:
        a = fetch_one(conn, "SELECT * FROM approvals WHERE id = %s", (timer["approval_id"],))
        bill = fetch_one(conn, "SELECT document_id FROM obligations WHERE id = %s", (timer["obligation_id"],))
    # The invoice isn't evidence for a payment it disagrees with.
    assert bill["document_id"] is None
    team = biz["team"]
    assert not approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED").won
    assert approval.decide(team["id"], team["users"]["APPROVER"], a["id"], a["payload_hash"], "APPROVED", reviewed=True).won


def test_a_second_invoice_cannot_take_a_period_another_invoice_claimed(biz, fake_model):
    mandate_id = active_mandate(biz)
    first = send_invoice(biz, fake_model, alex_invoice("A-1"))
    second = send_invoice(biz, fake_model, alex_invoice("A-2"))
    assert "already claimed" in second["stop"] and second["attached"] is False and second["obligation_id"] is None
    assert [p["id"] for p in live_payments(biz)] == [first["intent_id"]]
    assert len(occurrences(mandate_id)) == 1


def test_a_different_period_is_its_own_occurrence(biz, fake_model):
    mandate_id = active_mandate(biz)
    first = send_invoice(biz, fake_model, alex_invoice("A-1"))
    second = send_invoice(biz, fake_model, alex_invoice("A-2", period="2099-04"))
    assert first["intent_id"] and second["intent_id"] and len(occurrences(mandate_id)) == 2
    # Last month's payment under the same Mandate isn't a near-duplicate of this one.
    assert not any(r["rule_id"] == "NEAR_DUPLICATE" for r in second["reasons"]), second["reasons"]


def test_fuzzy_match_cannot_override_a_changed_destination(biz, fake_model):
    mandate_id = active_mandate(biz)
    result = send_invoice(biz, fake_model, alex_invoice(payment={"wallet_address": POISONED_WALLET}))
    assert result["decision"] == "BLOCK" and result["intent_id"] is None
    assert any(r["rule_id"] == "ADDRESS_LOOKALIKE" for r in result["reasons"])
    assert any(r["rule_id"] == "MANDATE_DESTINATION_DIFFERS" for r in result["reasons"])
    assert occurrences(mandate_id) == []
    # The period is still open for the real payment.
    assert fire(biz, mandate_id)["status"] == "AWAITING_APPROVAL"


def test_fuzzy_match_cannot_override_a_changed_destination_when_the_timer_already_claimed(biz, fake_model):
    mandate_id = active_mandate(biz)
    timer = fire(biz, mandate_id)
    result = send_invoice(biz, fake_model, alex_invoice(payment={"wallet_address": POISONED_WALLET}))
    assert result["decision"] == "BLOCK" and not result.get("attached")
    with tx() as conn:
        assert fetch_one(conn, "SELECT document_id FROM obligations WHERE id = %s", (timer["obligation_id"],))["document_id"] is None
    assert [p["id"] for p in live_payments(biz)] == [timer["intent_id"]]


def test_fuzzy_match_cannot_override_a_different_currency(biz, fake_model):
    """A GBP Mandate and a USD invoice from the same supplier: no claim, no conversion, and a person is told."""
    team = biz["team"]
    with tx() as conn:
        conn.execute("INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, external_id) VALUES (%s, %s, 'USD account', 'SIMULATOR', 'FIAT_BANK', 'USD', %s)", (team["id"] + "_usd", team["id"], team["id"] + "_usd_ext"))
    mandate_id = active_mandate(biz, counterparty="Examplo", max_amount="480", asset="GBP")
    result = send_invoice(biz, fake_model, invoice_fields("F-USD", currency="USD", period=PERIOD))
    assert occurrences(mandate_id) == []
    assert result["decision"] == "BLOCK", result["reasons"]  # No Verified Destination takes USD.
    assert any(r["rule_id"] == "MANDATE_CURRENCY_DIFFERS" for r in result["reasons"])


def test_no_active_mandate_means_no_claim(biz, fake_model):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "PAUSED")
    result = send_invoice(biz, fake_model, alex_invoice())
    assert occurrences(mandate_id) == [] and result["intent_id"]


def test_another_counterparty_does_not_match(biz, fake_model):
    mandate_id = active_mandate(biz)
    send_invoice(biz, fake_model, invoice_fields("F-1", period=PERIOD, payment={"iban": EXAMPLO_IBAN}))
    assert occurrences(mandate_id) == []


def test_exact_repeat_of_an_invoice_takes_no_claim_and_is_blocked(biz, fake_model):
    mandate_id = active_mandate(biz)
    send_invoice(biz, fake_model, alex_invoice("A-1"))
    again = send_invoice(biz, fake_model, alex_invoice("A-1", period="2099-05"))
    assert again["decision"] == "BLOCK" and any(r["rule_id"] == "DUPLICATE_INVOICE" for r in again["reasons"])
    assert [o["period"] for o in occurrences(mandate_id)] == [PERIOD]


def test_invoice_above_the_mandate_needs_review_with_both_figures(biz, fake_model):
    active_mandate(biz)
    result = send_invoice(biz, fake_model, alex_invoice(total="350.00"))
    reasons = [r for r in result["reasons"] if r["rule_id"] == "ABOVE_MANDATE_MAX"]
    assert reasons and "350.00" in reasons[0]["reason"] and "300.00" in reasons[0]["reason"]
    with tx() as conn:
        assert int(fetch_one(conn, "SELECT amount_minor FROM payment_intents WHERE id = %s", (result["intent_id"],))["amount_minor"]) == 350000000


def test_required_invoice_missing_opens_a_chase_then_the_invoice_prepares_the_payment(biz, fake_model):
    mandate_id = active_mandate(biz, required_evidence=["invoice"])
    timer = fire(biz, mandate_id)
    assert timer["decision"] == "NEEDS_REVIEW" or timer["decision"] == "NEEDS_INFORMATION"
    assert timer["intent_id"] is None, "A missing required invoice must not produce a payment."
    assert any(r["rule_id"] == "EVIDENCE_MISSING" for r in timer["reasons"])
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM exceptions WHERE expense_id = %s AND status = 'OPEN'", (timer["obligation_id"],))["n"] == 1
    invoice = send_invoice(biz, fake_model, alex_invoice(total="280.00"))
    assert invoice["status"] == "AWAITING_APPROVAL", invoice
    assert invoice["obligation_id"] == timer["obligation_id"]
    payments = live_payments(biz)
    assert len(payments) == 1 and int(payments[0]["amount_minor"]) == 280000000
    with tx() as conn:
        assert fetch_one(conn, "SELECT count(*) AS n FROM exceptions WHERE expense_id = %s AND status = 'OPEN'", (timer["obligation_id"],))["n"] == 0
    assert len(occurrences(mandate_id)) == 1


def test_invoice_after_the_timer_paid_becomes_the_expense_evidence(biz, fake_model):
    mandate_id = active_mandate(biz, amount="300")
    timer = fire(biz, mandate_id)
    assert pay_through(biz, timer)["state"] == "CONFIRMED"
    with tx() as conn:
        expense = fetch_one(conn, "SELECT expense_id, status FROM expense_versions WHERE intent_id = %s", (timer["intent_id"],))
    assert expense["status"] == "MISSING_EVIDENCE"
    invoice = send_invoice(biz, fake_model, alex_invoice())
    assert invoice["attached"] is True and invoice["intent_id"] is None
    with tx() as conn:
        latest = fetch_one(conn, "SELECT status FROM expense_versions WHERE expense_id = %s ORDER BY version DESC LIMIT 1", (expense["expense_id"],))
        count = fetch_one(conn, "SELECT count(*) AS n FROM payment_intents WHERE team_id = %s", (biz["team"]["id"],))["n"]
    assert latest["status"] == "SUPPORTED" and count == 1


def test_invoice_arriving_while_the_timer_is_starting_is_picked_up(biz, fake_model):
    """The timer has claimed but hasn't recorded what's owed yet, which is the narrowest gap."""
    mandate_id = active_mandate(biz, required_evidence=["invoice"], amount="300")
    team = biz["team"]
    run_id = engine.create_run("prepare_occurrence", team["id"], {"mandate_id": mandate_id, "period": PERIOD}, "TIMER")
    with tx() as conn:
        assert recurring._claim(conn, team["id"], mandate_id, PERIOD, "TIMER", run_id)
    attached = send_invoice(biz, fake_model, alex_invoice())
    assert attached["attached"] is True
    assert live_payments(biz) == []
    with tx() as conn:
        occurrence = fetch_one(conn, "SELECT * FROM occurrences WHERE mandate_id = %s", (mandate_id,))
    state = {"run_id": run_id, "team_id": team["id"], "inputs": {"mandate_id": mandate_id, "period": PERIOD}, "mandate_id": mandate_id, "period": PERIOD, "occurrence_id": occurrence["id"], "asset": "USDC", "amount_minor": "300000000", "counterparty_id": biz["alex"], "answers": {}, "checks": []}
    state.update(recurring.check_mandate({**state, **_registered(team, state)}) or {})
    assert state["document_id"] == occurrence["document_id"]
    assert not any(c["rule_id"] == "EVIDENCE_MISSING" for c in state["checks"])


def _registered(team, state):
    from payoai.routines.nodes.checks import register_obligation

    return {"obligation_id": register_obligation({**state, "inputs": {**state["inputs"], "amount_minor": "300000000", "asset": "USDC", "source_type": "OCCURRENCE"}})["obligation_id"]}


@pytest.mark.parametrize("attempt", range(2))
def test_timer_and_invoice_racing_still_make_one_payment(biz, fake_model, attempt):
    mandate_id = active_mandate(biz, amount="300")
    fake_model["fields"] = with_quotes(alex_invoice())
    team = biz["team"]
    document_id = store_document(team["id"], "invoice.txt", "text/plain", render_invoice(alex_invoice()).encode(), team["users"]["OPERATOR"])
    barrier = threading.Barrier(2)
    results, errors = {}, []

    def run(name, fn):
        try:
            barrier.wait(timeout=30)
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("timer", lambda: fire(biz, mandate_id))),
        threading.Thread(target=run, args=("invoice", lambda: engine.start("pay_bill", team["id"], {"document_id": document_id}, "CHAT", actor=team["users"]["OPERATOR"]))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not errors, errors
    assert len(occurrences(mandate_id)) == 1
    assert len(live_payments(biz)) == 1, results


# Prepare Occurrence's amount


def test_regular_amount_is_prepared_without_an_assumption(biz):
    mandate_id = active_mandate(biz, max_amount="300", amount="250")
    result = fire(biz, mandate_id)
    assert result["decision"] == "ELIGIBLE_FOR_APPROVAL", result["reasons"]
    with tx() as conn:
        assert int(fetch_one(conn, "SELECT amount_minor FROM payment_intents WHERE id = %s", (result["intent_id"],))["amount_minor"]) == 250000000


def test_no_regular_amount_assumes_the_maximum_only_with_review(biz):
    result = fire(biz, active_mandate(biz, max_amount="300"))
    assert result["decision"] == "NEEDS_REVIEW"
    assert any(r["rule_id"] == "AMOUNT_ASSUMED_MAXIMUM" and "300.00" in r["reason"] for r in result["reasons"])


def test_regular_amount_above_the_maximum_is_refused():
    with pytest.raises(ValueError):
        routine_inputs.validate("set_up_recurring", {"counterparty_name": "Alex", "max_amount": "100", "amount": "101", "asset": "USDC", "frequency": "MONTHLY", "day_of_period": 1, "valid_until": "2099-01-01"})


# Mandate limits


def test_a_destination_verified_later_is_not_covered_by_the_mandate(biz):
    team = biz["team"]
    mandate_id = active_mandate(biz, amount="300")
    with tx() as conn:
        conn.execute("UPDATE payment_destinations SET status = 'REVOKED' WHERE id = %s", (biz["alex_dest"],))
        conn.execute(
            "INSERT INTO payment_destinations (id, team_id, counterparty_id, rail, asset, masked_display, details, chain_id, token_contract, status, source) VALUES (%s, %s, %s, 'CRYPTO', 'USDC', 'new', %s, %s, %s, 'VERIFIED', 'test')",
            (team["id"] + "_new", team["id"], biz["alex"], '{"address": "0xabc"}', BASE_SEPOLIA, SYNTHETIC_USDC),
        )
    result = fire(biz, mandate_id)
    assert result["decision"] == "BLOCK" and result["intent_id"] is None
    assert any(r["rule_id"] == "NO_VERIFIED_DESTINATION" for r in result["reasons"])


def test_a_paused_mandate_leaves_the_period_open_for_later(biz):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "PAUSED")
    assert "active" in fire(biz, mandate_id)["stop"]
    assert occurrences(mandate_id) == []
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "ACTIVE")
    assert fire(biz, mandate_id)["status"] == "AWAITING_APPROVAL"


def test_a_revoked_mandate_cannot_be_reactivated(biz):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "REVOKED")
    with pytest.raises(ValueError):
        recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "ACTIVE")


def test_periods_before_activation_do_not_back_fill(biz):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "PAUSED")
    with tx() as conn:
        conn.execute("UPDATE automation_rules SET next_due = %s WHERE mandate_id = %s", (date.today() - timedelta(days=65), mandate_id))
    recurring.set_mandate_status(team["id"], team["users"]["APPROVER"], mandate_id, "ACTIVE")
    with tx() as conn:
        assert fetch_one(conn, "SELECT next_due FROM automation_rules WHERE mandate_id = %s", (mandate_id,))["next_due"] >= date.today()


def test_a_run_that_failed_after_claiming_does_not_lose_the_period(biz):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    run_id = engine.create_run("prepare_occurrence", team["id"], {"mandate_id": mandate_id, "period": PERIOD}, "TIMER")
    with tx() as conn:
        assert recurring._claim(conn, team["id"], mandate_id, PERIOD, "TIMER", run_id)
    # Still running: the claim holds.
    assert "already claimed" in fire(biz, mandate_id)["stop"]
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = 'FAILED' WHERE id = %s", (run_id,))
    assert fire(biz, mandate_id)["status"] == "AWAITING_APPROVAL"


def test_weekend_move_keeps_each_month_its_own_period():
    # 31 January 2026 is a Saturday, so it moves to 2 February but is still January's payment.
    due = recurring.next_due("MONTHLY", 31, date(2026, 1, 30))
    assert due == date(2026, 2, 2)
    assert recurring.period_of_due("MONTHLY", 31, due) == "2026-01"
    february = recurring.next_due("MONTHLY", 31, due)
    assert february == date(2026, 3, 2)  # 28 February 2026 is also a Saturday.
    assert recurring.period_of_due("MONTHLY", 31, february) == "2026-02"
    march = recurring.next_due("MONTHLY", 31, february)
    assert recurring.period_of_due("MONTHLY", 31, march) == "2026-03"
    # An ordinary date is unaffected, and it doesn't claim a nominal date it isn't near.
    assert recurring.period_of_due("MONTHLY", 15, date(2026, 6, 15)) == "2026-06"


def test_invoice_period_uses_exact_values_and_never_guesses():
    assert recurring.invoice_period({"period": "2099-03", "due_date": "2099-07-01"}, "MONTHLY") == "2099-03"
    assert recurring.invoice_period({"period": "March 2099", "due_date": "2099-07-01"}, "MONTHLY") == "2099-07"
    assert recurring.invoice_period({"period": "March 2099"}, "MONTHLY") is None
    assert recurring.invoice_period({"due_date": "2027-01-04"}, "WEEKLY") == "2027-W01"


def test_replace_bill_keeps_the_period_claim_on_the_corrected_bill(biz, fake_model):
    team = biz["team"]
    mandate_id = active_mandate(biz)
    first = send_invoice(biz, fake_model, alex_invoice("A-9"))
    fake_model["fields"] = with_quotes(alex_invoice("A-9", total="290.00"))
    replaced = engine.start("replace_bill", team["id"], routine_inputs.validate("replace_bill", {"obligation_id": first["obligation_id"], "reason": "Wrong total"}), "CONFIRMATION", actor=team["users"]["OPERATOR"])
    claim = occurrences(mandate_id)
    assert len(claim) == 1 and claim[0]["obligation_id"] == replaced["obligation_id"] != first["obligation_id"]
    assert "already claimed" in fire(biz, mandate_id)["stop"]
    assert [p["id"] for p in live_payments(biz)] == [replaced["intent_id"]]


def test_mandate_period_counts_in_cash_analysis_once(biz):
    team = biz["team"]
    active_mandate(biz, amount="300")
    result = engine.start("cash_analysis", team["id"], {"days": 7}, "TIMER")
    usdc = next(e for e in result["by_asset"] if e["asset"] == "USDC")
    assert usdc["bills_due_minor"] == 0 or any(b["status"] == "UPCOMING_OCCURRENCE" for b in usdc["bills_due"])


def test_unused_fraud_iban_is_blocked_even_with_a_mandate(biz, fake_model):
    mandate_id = active_mandate(biz, counterparty="Examplo", max_amount="480", asset="GBP")
    result = send_invoice(biz, fake_model, invoice_fields("F-9", period=PERIOD, payment={"iban": FRAUD_IBAN}))
    assert result["decision"] == "BLOCK" and occurrences(mandate_id) == []

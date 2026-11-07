"""Check Nodes shared by the payment Routines. All code, with no model and no Memory input."""

from datetime import datetime, timedelta, timezone

from payoai.compliance.rules import uk_vat
from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.domain.decide import same_details
from payoai.domain.money import format_minor, to_minor
from payoai.providers import get_provider
from payoai.providers.base import ProviderUnavailable
from payoai.routines.nodes.common import RunState, node, question, record_check

STALE_AFTER = timedelta(minutes=15)
LIVE_STATUSES = ("REGISTERED", "NEEDS_INFORMATION", "NEEDS_REVIEW", "VERIFIED", "PAYMENT_PREPARED", "PAYMENT_IN_PROGRESS", "PAID", "OUTCOME_UNKNOWN")
NEAR_DUPLICATE_DAYS = 35


def _normal_name(name: str) -> str:
    text = " ".join(name.lower().replace(".", "").replace(",", "").split())
    for suffix in (" limited", " ltd", " llp", " plc", " inc"):
        text = text.removesuffix(suffix)
    return text


@node("Match Counterparty")
def match_counterparty(state: RunState, ctx) -> dict:
    chosen = (state.get("answers") or {}).get("counterparty_id") or state["inputs"].get("counterparty_id")
    name = state["inputs"].get("counterparty_name") or (state.get("fields") or {}).get("supplier_name")
    with tx() as conn:
        rows = fetch_all(conn, "SELECT id, display_name, legal_name FROM counterparties WHERE team_id = %s", (state["team_id"],))
    if chosen:
        if any(r["id"] == chosen for r in rows):
            return {"counterparty_id": chosen}
        return {"stop": "That counterparty isn't in this team."}
    if not name:
        return {"stop": "No counterparty was named."}
    target = _normal_name(name)
    exact = [r for r in rows if target in (_normal_name(r["display_name"]), _normal_name(r["legal_name"] or ""))]
    partial = exact or [r for r in rows if target and (target in _normal_name(r["display_name"]) or target in _normal_name(r["legal_name"] or ""))]
    if len(partial) == 1:
        return {"counterparty_id": partial[0]["id"]}
    if len(partial) > 1:
        return question("counterparty_id", f"Which {name} do you mean?", [{"value": r["id"], "label": r["legal_name"] or r["display_name"]} for r in partial])
    return {"stop": f"{name} isn't a known counterparty. Add and verify them first."}


@node("Register Obligation")
def register_obligation(state: RunState, ctx) -> dict:
    fields = state.get("fields") or {}
    inputs = state["inputs"]
    is_invoice = bool(state.get("document_id"))
    asset = ((fields.get("currency") if is_invoice else inputs.get("asset")) or "").upper() or None
    try:
        if is_invoice:
            amount = to_minor(fields["total"], asset) if fields.get("total") and asset else None
        else:
            amount = int(inputs["amount_minor"])
    except (ValueError, TypeError):
        amount = None
    obligation_id = new_id("obl")
    reference = fields.get("invoice_number") if is_invoice else None
    with tx() as conn:
        live = reference and fetch_one(
            conn,
            "SELECT id FROM obligations WHERE team_id = %s AND counterparty_id = %s AND invoice_reference = %s AND status <> ALL(%s)",
            (state["team_id"], state["counterparty_id"], reference, ["SUPERSEDED", "CANCELLED", "BLOCKED"]),
        )
        conn.execute(
            """
            INSERT INTO obligations (id, team_id, counterparty_id, source_type, document_id, extraction_id, description, amount_minor, asset, due_date, invoice_reference, period, status, mandate_id, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                obligation_id, state["team_id"], state["counterparty_id"],
                "INVOICE" if is_invoice else inputs.get("source_type", "REQUEST"),
                state.get("document_id"), state.get("extraction_id"),
                fields.get("notes") if is_invoice else inputs.get("purpose"),
                amount, asset, _date(fields.get("due_date") or inputs.get("due_date")), reference, fields.get("period") or inputs.get("period"),
                "BLOCKED" if live else "REGISTERED", inputs.get("mandate_id"), state.get("actor"),
            ),
        )
    return {"obligation_id": obligation_id, "amount_minor": str(amount) if amount is not None else None, "asset": asset}


def _date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


@node("Load Registered Bill")
def load_registered(state: RunState, ctx) -> dict:
    """A registered bill reuses its file and extraction, and every check runs again."""
    with tx() as conn:
        row = fetch_one(
            conn,
            """
            SELECT o.*, e.fields FROM obligations o LEFT JOIN extractions e ON e.id = o.extraction_id
            WHERE o.id = %s AND o.team_id = %s
            """,
            (state["inputs"]["obligation_id"], state["team_id"]),
        )
    if row is None:
        return {"stop": "That bill wasn't found in this team."}
    if row["status"] in ("PAID", "PAYMENT_IN_PROGRESS", "OUTCOME_UNKNOWN", "SUPERSEDED", "CANCELLED"):
        return {"stop": f"This bill can't be prepared because its status is {row['status']}."}
    return {
        "obligation_id": row["id"], "counterparty_id": row["counterparty_id"], "document_id": row["document_id"],
        "extraction_id": row["extraction_id"], "fields": row["fields"] or {}, "failed_quotes": [],
        "amount_minor": str(int(row["amount_minor"])) if row["amount_minor"] is not None else None, "asset": row["asset"],
    }


@node("Check Duplicates")
def check_duplicates(state: RunState, ctx) -> dict:
    team, counterparty, obligation = state["team_id"], state["counterparty_id"], state["obligation_id"]
    fields = state.get("fields") or {}
    results = []
    with tx() as conn:
        me = fetch_one(conn, "SELECT o.*, d.sha256 FROM obligations o LEFT JOIN documents d ON d.id = o.document_id WHERE o.id = %s", (obligation,))
        others = fetch_all(
            conn,
            """
            SELECT o.*, d.sha256 FROM obligations o LEFT JOIN documents d ON d.id = o.document_id
            WHERE o.team_id = %s AND o.id <> %s AND o.status = ANY(%s)
            """,
            (team, obligation, list(LIVE_STATUSES)),
        )
    same_supplier = [o for o in others if o["counterparty_id"] == counterparty]
    reference = fields.get("invoice_number")
    exact = next((o for o in same_supplier if reference and o["invoice_reference"] == reference), None)
    same_file = next((o for o in others if me["sha256"] and o["sha256"] == me["sha256"]), None)
    if exact:
        results.append(record_check(state, "Check Duplicates", "DUPLICATE_INVOICE", "BLOCK", f"Invoice {reference} from this supplier is already recorded on bill {exact['id']}.", {"earlier": exact["id"]}))
    elif same_file:
        results.append(record_check(state, "Check Duplicates", "DUPLICATE_FILE", "BLOCK", f"The same file is already recorded on bill {same_file['id']}.", {"earlier": same_file["id"]}))
    else:
        since = datetime.now(timezone.utc) - timedelta(days=NEAR_DUPLICATE_DAYS)
        near = next((o for o in same_supplier if me["amount_minor"] is not None and o["amount_minor"] == me["amount_minor"] and (o["created_at"] >= since or (me["period"] and o["period"] == me["period"]))), None)
        if near:
            results.append(record_check(state, "Check Duplicates", "NEAR_DUPLICATE", "NEEDS_REVIEW", f"A similar payment of the same amount to this counterparty exists on {near['id']}. Repeat payments are often legitimate, so a person should check.", {"earlier": near["id"]}))
        else:
            results.append(record_check(state, "Check Duplicates", "NO_DUPLICATE", "PASS", "No matching invoice, file or recent similar payment."))
    if me["status"] == "BLOCKED" and not exact:
        results.append(record_check(state, "Check Duplicates", "DUPLICATE_INVOICE", "BLOCK", "This invoice number was already live when the bill was registered."))
    return {"checks": results}


@node("Check Compliance")
def check_compliance(state: RunState, ctx) -> dict:
    fields = state.get("fields") or {}
    results = [
        record_check(state, "Check Compliance", r["rule_id"], r["outcome"], r["reason"], r["evidence"])
        for r in uk_vat.evaluate(fields)
    ]
    for failure in state.get("failed_quotes") or []:
        results.append(record_check(state, "Extract Fields", "EXTRACTION_UNVERIFIED", "NEEDS_REVIEW", f"The extracted {failure['field']} couldn't be confirmed: {failure['reason']}.", failure))
    for text in fields.get("suspicious_text") or []:
        results.append(record_check(state, "Extract Fields", "SUSPICIOUS_TEXT", "NEEDS_REVIEW", "The document contains text aimed at an AI or asking to skip checks. It was ignored.", {"text": str(text)[:300]}))
    return {"checks": results}


@node("Check Amount")
def check_amount(state: RunState, ctx) -> dict:
    amount = int(state["amount_minor"]) if state.get("amount_minor") else None
    if amount is None:
        return {"checks": [record_check(state, "Check Amount", "AMOUNT_MISSING", "NEEDS_INFORMATION", "The amount is unknown.")]}
    with tx() as conn:
        cp = fetch_one(conn, "SELECT * FROM counterparties WHERE id = %s", (state["counterparty_id"],))
        paid = fetch_one(conn, "SELECT count(*) AS n FROM obligations WHERE counterparty_id = %s AND status = 'PAID'", (state["counterparty_id"],))["n"]
        corrected = fetch_one(conn, "SELECT count(*) AS n FROM field_corrections WHERE obligation_id = %s AND is_money_field", (state["obligation_id"],))["n"]
    low, high = cp["usual_amount_min_minor"], cp["usual_amount_max_minor"]
    asset = state.get("asset") or ""
    results = []
    if low is None and high is None and paid == 0:
        results.append(record_check(state, "Check Amount", "FIRST_PAYMENT", "NEEDS_REVIEW", f"This is the first payment to {cp['display_name']}, so there's no usual amount to compare with."))
    elif (low is not None and amount < low) or (high is not None and amount > high):
        results.append(record_check(state, "Check Amount", "UNUSUAL_AMOUNT", "NEEDS_REVIEW", f"{format_minor(amount, asset)} is outside this counterparty's usual range. Unusual isn't proof of a problem.", {"low": str(low), "high": str(high)}))
    else:
        results.append(record_check(state, "Check Amount", "AMOUNT_NORMAL", "PASS", "The amount is within the usual range."))
    if corrected:
        results.append(record_check(state, "Check Amount", "CORRECTED_MONEY_FIELD", "NEEDS_REVIEW", "A person corrected a money field, so it differs from the document."))
    return {"checks": results}


@node("Select Destination")
def select_destination(state: RunState, ctx) -> dict:
    """Only uses Verified Destinations. Details on an invoice or in chat are never a source."""
    chosen = (state.get("answers") or {}).get("destination_id")
    asset = state.get("asset")
    with tx() as conn:
        rows = fetch_all(
            conn,
            "SELECT * FROM payment_destinations WHERE team_id = %s AND counterparty_id = %s AND status = 'VERIFIED' AND asset = %s ORDER BY created_at",
            (state["team_id"], state["counterparty_id"], asset),
        )
    if chosen:
        if any(r["id"] == chosen for r in rows):
            return {"destination_id": chosen}
        return {"stop": "That destination isn't verified for this counterparty."}
    if not rows:
        return {"destination_id": None}
    stated = (state.get("fields") or {}).get("payment_details") or {}
    matching = [r for r in rows if _matches(r, stated)]
    if len(matching) == 1:
        return {"destination_id": matching[0]["id"]}
    if len(rows) == 1:
        return {"destination_id": rows[0]["id"]}
    if state.get("inputs", {}).get("batch"):
        return {"destination_id": None}
    return question("destination_id", "Which verified account should this go to?", [{"value": r["id"], "label": r["masked_display"]} for r in rows])


def _matches(destination: dict, stated: dict) -> bool:
    details = destination["details"]
    if destination["rail"] == "CRYPTO":
        return same_details(stated.get("wallet_address"), details.get("address"))
    if stated.get("iban"):
        return same_details(stated.get("iban"), details.get("iban"))
    return same_details(stated.get("account_number"), details.get("account_number")) and same_details(stated.get("sort_code"), details.get("sort_code"))


@node("Check Destination")
def check_destination(state: RunState, ctx) -> dict:
    stated = (state.get("fields") or {}).get("payment_details") or {}
    if not state.get("destination_id"):
        with tx() as conn:
            revoked = fetch_one(conn, "SELECT id FROM payment_destinations WHERE team_id = %s AND counterparty_id = %s AND status = 'REVOKED' LIMIT 1", (state["team_id"], state["counterparty_id"]))
        reason = "This counterparty's destination was revoked and there's no other verified one." if revoked else f"There's no Verified Destination that takes {state.get('asset')} for this counterparty."
        return {"checks": [record_check(state, "Check Destination", "NO_VERIFIED_DESTINATION", "BLOCK", reason)]}
    with tx() as conn:
        destination = fetch_one(conn, "SELECT * FROM payment_destinations WHERE id = %s", (state["destination_id"],))
    if destination["rail"] == "CRYPTO":
        results = _check_crypto(state, destination, stated)
    else:
        results = _check_fiat(state, destination, stated)
    if not any(r["outcome"] == "BLOCK" for r in results):
        results.append(record_check(state, "Check Destination", "DESTINATION_VERIFIED", "PASS", f"Paying the Verified Destination {destination['masked_display']}."))
    return {"checks": results}


def _check_fiat(state, destination, stated) -> list[dict]:
    details = destination["details"]
    if stated.get("wallet_address"):
        return [record_check(state, "Check Destination", "RAIL_MISMATCH", "BLOCK", "The invoice asks for a crypto payment, but the Verified Destination is a bank account.")]
    if stated.get("iban") and not same_details(stated["iban"], details.get("iban")):
        return [record_check(state, "Check Destination", "DESTINATION_CHANGED", "BLOCK", "The IBAN on the invoice doesn't match the Verified Destination. Verify it through a channel already on file.", {"stated": stated["iban"], "verified": destination["masked_display"]})]
    if stated.get("account_number") and not (same_details(stated.get("account_number"), details.get("account_number")) and same_details(stated.get("sort_code"), details.get("sort_code"))):
        return [record_check(state, "Check Destination", "DESTINATION_CHANGED", "BLOCK", "The bank details on the invoice don't match the Verified Destination. Verify them through a channel already on file.", {"verified": destination["masked_display"]})]
    return []


def _check_crypto(state, destination, stated) -> list[dict]:
    """Full-address equality, exact chain and exact token contract. A lookalike is never a match."""
    results = []
    details = destination["details"]
    stated_address = stated.get("wallet_address")
    if stated.get("iban") or stated.get("account_number"):
        results.append(record_check(state, "Check Destination", "RAIL_MISMATCH", "BLOCK", "The invoice asks for a bank payment, but the Verified Destination is a wallet."))
    if stated_address and not same_details(stated_address, details.get("address")):
        lookalike = stated_address[:6].lower() == details["address"][:6].lower() and stated_address[-4:].lower() == details["address"][-4:].lower()
        reason = "The wallet on the invoice looks like the verified one at both ends, but the full address differs. This is the address-poisoning pattern." if lookalike else "The wallet on the invoice doesn't match the Verified Destination. Verify it through a channel already on file."
        results.append(record_check(state, "Check Destination", "ADDRESS_LOOKALIKE" if lookalike else "DESTINATION_CHANGED", "BLOCK", reason, {"stated": stated_address, "verified": destination["masked_display"]}))
    stated_chain = stated.get("chain_id")
    if stated_chain not in (None, "") and str(stated_chain) != str(destination["chain_id"]):
        results.append(record_check(state, "Check Destination", "WRONG_CHAIN", "BLOCK", f"The invoice names chain {stated_chain}, but the verified wallet is on chain {destination['chain_id']}."))
    stated_token = stated.get("token_contract")
    if stated_token and not same_details(stated_token, destination["token_contract"]):
        results.append(record_check(state, "Check Destination", "WRONG_TOKEN", "BLOCK", "The token contract on the invoice isn't the verified one. A matching symbol isn't enough."))
    return results


@node("Select Source Account")
def select_source_account(state: RunState, ctx) -> dict:
    chosen = (state.get("answers") or {}).get("source_account_id") or state["inputs"].get("source_account_id")
    with tx() as conn:
        rows = fetch_all(conn, "SELECT * FROM financial_accounts WHERE team_id = %s AND asset = %s AND status = 'ACTIVE' ORDER BY name", (state["team_id"], state.get("asset")))
    if chosen:
        if any(r["id"] == chosen for r in rows):
            return {"source_account_id": chosen}
        return {"stop": "That source account doesn't hold this asset in this team."}
    if len(rows) == 1:
        return {"source_account_id": rows[0]["id"]}
    if not rows:
        return {"source_account_id": None}
    if state.get("inputs", {}).get("batch"):
        return {"source_account_id": rows[0]["id"]}
    return question("source_account_id", "Which account should this be paid from?", [{"value": r["id"], "label": r["name"]} for r in rows])


def refresh_balance(account: dict) -> dict | None:
    """Saves a new snapshot, or returns the last one if the provider can't be reached."""
    try:
        balance = get_provider(account["provider"]).get_balance(account["external_id"])
    except ProviderUnavailable:
        balance = None
    with tx() as conn:
        if balance is not None:
            conn.execute(
                "INSERT INTO balance_snapshots (id, team_id, account_id, confirmed_minor, available_minor, as_of, source) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (new_id("bal"), account["team_id"], account["id"], balance.confirmed_minor, balance.available_minor, balance.as_of, balance.source),
            )
        return fetch_one(conn, "SELECT * FROM balance_snapshots WHERE account_id = %s ORDER BY as_of DESC, created_at DESC LIMIT 1", (account["id"],))


def account_position(account: dict, snapshot: dict) -> dict:
    """Counts each pound once. Provider figures may already exclude payments in flight."""
    with tx() as conn:
        in_flight = fetch_one(
            conn,
            "SELECT COALESCE(sum(amount_minor), 0) AS n FROM payment_intents WHERE source_account_id = %s AND status = ANY(%s)",
            (account["id"], ["SUBMITTED", "PENDING", "UNKNOWN", "NEEDS_MANUAL_REVIEW"]),
        )["n"]
        reserved = fetch_one(
            conn,
            "SELECT COALESCE(sum(amount_minor), 0) AS n FROM payment_intents WHERE source_account_id = %s AND status = ANY(%s)",
            (account["id"], ["APPROVED", "SUBMITTING"]),
        )["n"]
    available = int(snapshot["available_minor"])
    deducted = 0 if account["available_excludes_in_flight"] else int(in_flight)
    return {
        "available_minor": available,
        "in_flight_minor": int(in_flight),
        "in_flight_deducted_minor": deducted,
        "approved_minor": int(reserved),
        "reserve_minor": int(account["reserve_minor"]),
        "spendable_minor": available - deducted - int(reserved) - int(account["reserve_minor"]),
        "as_of": snapshot["as_of"].isoformat(),
        "stale": datetime.now(timezone.utc) - snapshot["as_of"] > STALE_AFTER,
    }


@node("Check Cash")
def check_cash(state: RunState, ctx) -> dict:
    if not state.get("source_account_id"):
        return {"checks": [record_check(state, "Check Cash", "NO_SOURCE_ACCOUNT", "NEEDS_INFORMATION", f"No account holds {state.get('asset')}. Currency is never converted automatically.")]}
    with tx() as conn:
        account = fetch_one(conn, "SELECT * FROM financial_accounts WHERE id = %s", (state["source_account_id"],))
    snapshot = refresh_balance(account)
    if snapshot is None:
        return {"checks": [record_check(state, "Check Cash", "BALANCE_UNKNOWN", "NEEDS_REVIEW", "The balance can't be read, so affordability is unknown.")]}
    position = account_position(account, snapshot)
    amount = int(state["amount_minor"] or 0) + fee_cap(account["rail"])
    results = []
    if position["stale"]:
        results.append(record_check(state, "Check Cash", "BALANCE_STALE", "NEEDS_REVIEW", f"The balance is from {position['as_of']}, so it may be out of date.", position))
    if amount > position["spendable_minor"]:
        shortfall = amount - position["spendable_minor"]
        results.append(record_check(state, "Check Cash", "CASH_BELOW_RESERVE", "NEEDS_REVIEW", f"Paying this would leave the account {format_minor(shortfall, account['asset'])} short of its reserve.", position))
    if not results:
        results.append(record_check(state, "Check Cash", "CASH_OK", "PASS", "The account can cover this and keep its reserve.", position))
    return {"checks": results}


def fee_cap(rail: str) -> int:
    return 1_000_000 if rail == "CRYPTO" else 1200


__all__ = ["Jsonb"]

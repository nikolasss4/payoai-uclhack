"""Add Counterparty, and Verify Destination, which is a dedicated action that's never done in chat."""

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.money.approval import cancel_pending
from payoai.money.locks import lock_obligation
from payoai.routines.nodes.common import RunState, node
from payoai.routines.nodes.decision import notify_approvers
from payoai.trace import TraceContext, audit, step

STRONG = ("CALL_BACK", "SIGNED_CONTRACT", "CRYPTO_WALLET", "NEW_RELATIONSHIP")
CHANNEL_REQUIRED = ("CALL_BACK", "CRYPTO_WALLET", "SIGNED_CONTRACT")


class VerificationRefused(ValueError):
    pass


def _mask(details: dict) -> str:
    if details.get("address"):
        return f"{details['address'][:6]}…{details['address'][-4:]}"
    if details.get("iban"):
        return f"{details['iban'][:4]}…{details['iban'][-4:]}"
    return f"{details.get('sort_code', '')} …{str(details.get('account_number', ''))[-4:]}"


@node("Add Counterparty")
def add_counterparty(state: RunState, ctx) -> dict:
    """New details are saved as UNVERIFIED, and an existing Verified Destination stays valid."""
    team, inputs = state["team_id"], state["inputs"]
    with tx() as conn:
        existing = fetch_one(conn, "SELECT id FROM counterparties WHERE team_id = %s AND lower(display_name) = lower(%s)", (team, inputs["display_name"]))
        counterparty_id = existing["id"] if existing else new_id("cp")
        if not existing:
            conn.execute(
                "INSERT INTO counterparties (id, team_id, display_name, legal_name, entity_type, classifications, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (counterparty_id, team, inputs["display_name"], inputs.get("legal_name"), inputs.get("entity_type", "business"), Jsonb(inputs.get("classifications", [])), state.get("actor")),
            )
        # A phone number only joins the record when the contact is new, so a request can't supply its own call-back number.
        if inputs.get("contact_phone") and not existing:
            conn.execute("INSERT INTO contact_channels (id, team_id, counterparty_id, kind, value) VALUES (%s, %s, %s, 'phone', %s)", (new_id("chn"), team, counterparty_id, inputs["contact_phone"]))
        details = {k: inputs[k] for k in ("iban", "sort_code", "account_number") if inputs.get(k)}
        if inputs.get("wallet_address"):
            details["address"] = inputs["wallet_address"]
        destination_id = None
        if details:
            destination_id = new_id("dest")
            conn.execute(
                "INSERT INTO payment_destinations (id, team_id, counterparty_id, rail, asset, masked_display, details, chain_id, token_contract, status, source, added_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'UNVERIFIED', %s, %s)",
                (destination_id, team, counterparty_id, "CRYPTO" if inputs.get("wallet_address") else "FIAT_BANK", (inputs.get("asset") or ("USDC" if inputs.get("wallet_address") else "GBP")).upper(), _mask(details), Jsonb(details), inputs.get("chain_id"), inputs.get("token_contract"), inputs.get("source", "chat"), state.get("actor")),
            )
        audit(conn, team, "USER", state.get("actor"), "CounterpartySaved", "counterparty", counterparty_id, {"new": not existing, "destination_id": destination_id})
    return {"result": {"counterparty_id": counterparty_id, "destination_id": destination_id, "destination_status": "UNVERIFIED" if destination_id else None, "note": "New details can't be paid until an Approver verifies them on the verification screen."}}


CHANNEL_KINDS = ("phone", "email", "contract")


def add_channel(team_id: str, user_id: str, counterparty_id: str, kind: str, value: str) -> dict:
    """Records how to reach a contact. A channel only counts for details that come in after it, so adding one can't verify details already waiting."""
    kind, value = (kind or "").strip().lower(), (value or "").strip()
    if kind not in CHANNEL_KINDS:
        raise VerificationRefused("Choose phone, email or contract.")
    if not value:
        raise VerificationRefused("Enter the phone number, email or contract reference.")
    with tx() as conn:
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] != "APPROVER":
            raise VerificationRefused("Only an Approver can add a contact channel.")
        if not fetch_one(conn, "SELECT id FROM counterparties WHERE id = %s AND team_id = %s", (counterparty_id, team_id)):
            raise VerificationRefused("That contact isn't in this team.")
        channel = fetch_one(
            conn, "INSERT INTO contact_channels (id, team_id, counterparty_id, kind, value) VALUES (%s, %s, %s, %s, %s) RETURNING id, kind, value, added_at",
            (new_id("chn"), team_id, counterparty_id, kind, value),
        )
        audit(conn, team_id, "USER", user_id, "ContactChannelAdded", "counterparty", counterparty_id, {"channel_id": channel["id"], "kind": kind})
    return channel


def verify_destination(team_id: str, user_id: str, destination_id: str, method: str, evidence: str, channel_id: str | None = None) -> dict:
    """The contact channel must have been on file before the details came in."""
    if method == "PROVIDER_CHECK":
        raise VerificationRefused("A provider name match only supports another method. It can't verify a destination alone.")
    if method not in STRONG:
        raise VerificationRefused(f"Unknown method {method}.")
    if not (evidence or "").strip():
        raise VerificationRefused("Evidence is required.")
    run_id = new_id("run")
    with tx() as conn:
        conn.execute("INSERT INTO routine_runs (id, team_id, routine, trigger_type, triggered_by, inputs, status) VALUES (%s, %s, 'verify_destination', 'TAP', %s, %s, 'RUNNING')", (run_id, team_id, user_id, Jsonb({"destination_id": destination_id, "method": method})))
    ctx = TraceContext(team_id, run_id=run_id)
    with step(ctx, "TAP", "Verify Destination", {"destination_id": destination_id, "method": method}) as s:
        with tx() as conn:
            role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
            if not role or role["role"] != "APPROVER":
                raise VerificationRefused("Only an Approver can verify a destination.")
            destination = fetch_one(conn, "SELECT * FROM payment_destinations WHERE id = %s AND team_id = %s FOR UPDATE", (destination_id, team_id))
            if destination is None:
                raise VerificationRefused("That destination isn't in this team.")
            if destination["status"] != "UNVERIFIED":
                raise VerificationRefused(f"This destination is {destination['status']}.")
            if method in CHANNEL_REQUIRED:
                channel = channel_id and fetch_one(conn, "SELECT * FROM contact_channels WHERE id = %s AND counterparty_id = %s", (channel_id, destination["counterparty_id"]))
                if not channel:
                    raise VerificationRefused("Choose a contact channel already on file for this counterparty.")
                if channel["added_at"] >= destination["created_at"]:
                    raise VerificationRefused("That contact was added after these details came in, so it isn't independent.")
                if method == "SIGNED_CONTRACT" and channel["kind"] != "contract":
                    raise VerificationRefused("Choose a contract already on file.")
            same_person = destination["added_by"] == user_id
            conn.execute(
                "INSERT INTO destination_verifications (id, team_id, destination_id, method, channel_id, evidence, verified_by, same_person_added) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id("ver"), team_id, destination_id, method, channel_id, evidence, user_id, same_person),
            )
            conn.execute("UPDATE payment_destinations SET status = 'VERIFIED' WHERE id = %s", (destination_id,))
            audit(conn, team_id, "USER", user_id, "DestinationVerified", "payment_destination", destination_id, {"method": method, "same_person": same_person, "weaker_trust": method == "NEW_RELATIONSHIP"})
        s.output = {"status": "VERIFIED", "same_person": same_person}
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = 'COMPLETED', updated_at = now() WHERE id = %s", (run_id,))
    return {"status": "VERIFIED", "same_person": same_person, "note": "Existing payments aren't updated. They need preparing and approving again."}


def revoke_destination(team_id: str, user_id: str, destination_id: str, reason: str) -> dict:
    """Takes effect immediately and cancels pending approvals using this destination."""
    with tx() as conn:
        role = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        if not role or role["role"] != "APPROVER":
            raise VerificationRefused("Only an Approver can revoke a destination.")
        row = conn.execute("UPDATE payment_destinations SET status = 'REVOKED', revoked_at = now() WHERE id = %s AND team_id = %s RETURNING id", (destination_id, team_id)).fetchone()
        if row is None:
            raise VerificationRefused("That destination isn't in this team.")
        intents = fetch_all(conn, "SELECT id, obligation_id FROM payment_intents WHERE destination_id = %s AND status = 'READY_FOR_APPROVAL' ORDER BY obligation_id", (destination_id,))
        for intent in intents:
            lock_obligation(conn, intent["obligation_id"])
            if cancel_pending(conn, intent["id"]):
                conn.execute("UPDATE payment_intents SET status = 'CANCELLED', updated_at = now() WHERE id = %s", (intent["id"],))
                conn.execute("UPDATE obligations SET status = 'NEEDS_REVIEW', updated_at = now() WHERE id = %s", (intent["obligation_id"],))
        audit(conn, team_id, "USER", user_id, "DestinationRevoked", "payment_destination", destination_id, {"reason": reason, "cancelled": len(intents)})
        notify_approvers(conn, team_id, "DESTINATION_REVOKED", {"destination_id": destination_id}, f"revoked:{destination_id}")
    return {"status": "REVOKED", "cancelled_approvals": len(intents)}

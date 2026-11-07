"""Select Source Account. It's kept apart from the check Nodes because it may read Memory, and they may not.

A saved hint only stands in for a question that would otherwise be asked. It must name an account
that already fits, and the approval card always says it was used.
"""

from payoai.db import fetch_all, fetch_one, tx
from payoai.memory import learner, preferences_for
from payoai.routines.nodes.common import RunState, node, question


@node("Select Source Account")
def select_source_account(state: RunState, ctx) -> dict:
    answered = (state.get("answers") or {}).get("source_account_id")
    chosen = answered or state["inputs"].get("source_account_id")
    with tx() as conn:
        rows = fetch_all(conn, "SELECT * FROM financial_accounts WHERE team_id = %s AND asset = %s AND status = 'ACTIVE' ORDER BY name", (state["team_id"], state.get("asset")))
        destination = state.get("destination_id") and fetch_one(conn, "SELECT rail, chain_id, token_contract FROM payment_destinations WHERE id = %s AND team_id = %s", (state["destination_id"], state["team_id"]))
    if destination:
        rows = [r for r in rows if _can_reach(r, destination)]
    if chosen:
        if any(r["id"] == chosen for r in rows):
            if answered:
                # Answering this question is a repeated action Auto Mode can count. It only ever becomes a suggestion.
                learner.observe(state["team_id"], state.get("actor"), "source_account_hint", state.get("asset"), {"account_id": chosen}, source_id=state["run_id"])
            return {"source_account_id": chosen}
        return {"stop": "That source account doesn't hold this asset in this team."}
    if len(rows) == 1:
        return {"source_account_id": rows[0]["id"]}
    if not rows:
        return {"source_account_id": None}
    if state.get("inputs", {}).get("batch"):
        return {"source_account_id": rows[0]["id"]}
    suggested = _suggestion(state, rows)
    if suggested:
        return suggested
    return question("source_account_id", "Which account should this be paid from?", [{"value": r["id"], "label": r["name"]} for r in rows])


def _can_reach(account: dict, destination: dict) -> bool:
    """Same rail, and for crypto the same chain and token. Sharing an asset code isn't enough."""
    if account["rail"] != destination["rail"]:
        return False
    if destination["rail"] != "CRYPTO":
        return True
    same_chain = None in (account["chain_id"], destination["chain_id"]) or account["chain_id"] == destination["chain_id"]
    same_token = None in (account["token_contract"], destination["token_contract"]) or str(account["token_contract"]).lower() == str(destination["token_contract"]).lower()
    return same_chain and same_token


def _suggestion(state: RunState, rows: list[dict]) -> dict | None:
    hints = preferences_for(state["team_id"], state.get("actor"), ("source_account_hint",), state.get("asset"))
    for hint in hints:
        account = next((r for r in rows if r["id"] == (hint["value"] or {}).get("account_id")), None)
        if account:
            note = {
                "kind": "source_account_suggestion", "memory_id": hint["id"], "account_id": account["id"],
                "text": f"Paying from {account['name']} because of a saved preference. It's a suggestion and not a check. To use another account, reject this and start again.",
            }
            return {"source_account_id": account["id"], "source_account_note": note}
    return None

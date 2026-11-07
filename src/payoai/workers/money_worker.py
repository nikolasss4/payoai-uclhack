"""Executes and reconciles payments. It holds provider and signing keys, and never calls an LLM."""

from payoai.settings import load_env

load_env(".env.shared", ".env.money")

from payoai.db import fetch_all, tx  # noqa: E402
from payoai.money import execute, reconcile  # noqa: E402
from payoai.routines.nodes.checks import refresh_balance  # noqa: E402
from payoai.routines import purchases  # noqa: E402
from payoai.workers.loop import run_forever  # noqa: E402


def handle_execute(job: dict) -> None:
    execute.execute(job["team_id"], job["payload"]["intent_id"], job["payload"]["approval_id"])


def handle_reconcile(job: dict) -> None:
    reconcile.reconcile(job["payload"]["attempt_id"], job["payload"].get("trigger", "TIMER"))


def handle_checkout(job: dict) -> None:
    purchases.execute_checkout(job["team_id"], job["payload"]["purchase_id"])


def handle_refresh(job: dict) -> None:
    with tx() as conn:
        accounts = fetch_all(conn, "SELECT * FROM financial_accounts WHERE team_id = %s AND status = 'ACTIVE'", (job["team_id"],))
    for account in accounts:
        refresh_balance(account)


HANDLERS = {"refresh_balances": handle_refresh, "execute_payment": handle_execute, "reconcile_payment": handle_reconcile, "execute_checkout": handle_checkout}

if __name__ == "__main__":
    run_forever("money-worker", HANDLERS)

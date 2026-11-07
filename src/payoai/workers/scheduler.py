"""Creates jobs on a timer and does none of the work itself."""

import time
from datetime import date

from payoai.settings import load_env

load_env(".env.shared")

from payoai.db import fetch_all, tx  # noqa: E402
from payoai.queue import enqueue, recover_stale  # noqa: E402
from payoai.routines.nodes.decision import notify_approvers  # noqa: E402
from payoai.routines.recurring import fire_due_occurrences  # noqa: E402

TICK_SECONDS = 60


def tick() -> None:
    recover_stale()
    fire_due_occurrences()
    today = date.today().isoformat()
    with tx() as conn:
        for team in fetch_all(conn, "SELECT id FROM teams WHERE NOT is_eval"):
            enqueue(conn, "cash_analysis", team["id"], {}, dedupe_key=f"cash:{team['id']}:{today}")
        stale = fetch_all(conn, "SELECT id, team_id, expense_id FROM exceptions WHERE status = 'OPEN' AND COALESCE(last_reminded_at, created_at) < now() - interval '7 days'")
        for row in stale:
            notify_approvers(conn, row["team_id"], "MISSING_DOCUMENT_REMINDER", {"expense_id": row["expense_id"]}, f"remind:{row['id']}:{today}")
            conn.execute("UPDATE exceptions SET last_reminded_at = now() WHERE id = %s", (row["id"],))


if __name__ == "__main__":
    print("scheduler started", flush=True)
    while True:
        try:
            tick()
        except Exception as exc:  # A failed tick is reported, and the next one still runs.
            print(f"scheduler tick failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(TICK_SECONDS)

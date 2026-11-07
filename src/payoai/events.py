"""Typed events shown to the user. Code writes them, so a model can't report a false status."""

from payoai.db import Jsonb, fetch_one, tx


def emit(team_id: str, chat_id: str | None, kind: str, payload: dict, turn_id: str | None = None, run_id: str | None = None) -> None:
    if chat_id is None:
        return
    with tx() as conn:
        conn.execute(
            "INSERT INTO chat_events (team_id, chat_id, turn_id, run_id, kind, payload) VALUES (%s, %s, %s, %s, %s, %s)",
            (team_id, chat_id, turn_id, run_id, kind, Jsonb(payload)),
        )


def chat_of_run(run_id: str) -> str | None:
    with tx() as conn:
        row = fetch_one(conn, "SELECT chat_id FROM routine_runs WHERE id = %s", (run_id,))
    return row["chat_id"] if row else None

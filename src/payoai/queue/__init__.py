"""Postgres job queue. Workers claim jobs with SKIP LOCKED, and a dedupe key stops double jobs."""

from datetime import datetime

import psycopg

from payoai.db import Jsonb, fetch_one, new_id, tx


def enqueue(
    conn: psycopg.Connection,
    kind: str,
    team_id: str,
    payload: dict,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> bool:
    """Adds a job inside the caller's transaction. Returns False if the dedupe key already exists."""
    row = conn.execute(
        """
        INSERT INTO jobs (id, kind, team_id, payload, dedupe_key, run_at)
        VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
        """,
        (new_id("job"), kind, team_id, Jsonb(payload), dedupe_key, run_at),
    ).fetchone()
    return row is not None


def claim(worker: str, kinds: list[str]) -> dict | None:
    with tx() as conn:
        return fetch_one(
            conn,
            """
            UPDATE jobs SET status = 'CLAIMED', locked_by = %s, locked_at = now(), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'QUEUED' AND kind = ANY(%s) AND run_at <= now()
                  AND NOT (kind = 'run_turn' AND EXISTS (
                    SELECT 1 FROM jobs busy WHERE busy.kind = 'run_turn' AND busy.status = 'CLAIMED'
                      AND busy.payload->>'chat_id' = jobs.payload->>'chat_id'))
                ORDER BY (kind IN ('run_turn', 'run_routine')) DESC, run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """,
            (worker, kinds),
        )


def finish(job_id: str, error: str | None = None) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE jobs SET status = %s, last_error = %s WHERE id = %s",
            ("FAILED" if error else "DONE", error, job_id),
        )


def recover_stale(minutes: int = 10) -> int:
    """Requeues jobs from a crashed worker. Money jobs are safe: Execute Payment checks for an existing attempt."""
    with tx() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'QUEUED' WHERE status = 'CLAIMED' AND locked_at < now() - make_interval(mins => %s)",
            (minutes,),
        )
        return cur.rowcount

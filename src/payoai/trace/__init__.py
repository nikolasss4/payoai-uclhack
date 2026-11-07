"""Trace Steps and Audit Events.

A step is saved when it starts and updated when it ends, so a crash still leaves a record.
If the start can't be saved, the step doesn't run.
"""

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterator

import psycopg

from payoai.db import Jsonb, new_id, tx

SECRET_KEYS = re.compile(r"(api_?key|secret|token$|password|private|signed_tx|authorization)", re.I)
FULL_DETAIL_KEYS = {"iban", "account_number", "sort_code", "address", "destination_details", "details"}
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
ADDRESS = re.compile(r"\b0x[a-fA-F0-9]{40}\b")


def redact(value: Any) -> Any:
    """Removes secrets and full account details before anything is saved or logged."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if SECRET_KEYS.search(str(key)) or str(key) in FULL_DETAIL_KEYS:
                out[key] = "[redacted]"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = IBAN.sub(lambda m: f"{m.group()[:4]}…{m.group()[-4:]}", value)
        return ADDRESS.sub(lambda m: f"{m.group()[:6]}…{m.group()[-4:]}", text)
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


@dataclass
class Step:
    id: str
    output: Any = None
    evidence_ids: list[str] = field(default_factory=list)
    model: str | None = None
    thinking_level: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_micros: int | None = None


@dataclass(frozen=True)
class TraceContext:
    team_id: str
    run_id: str | None = None
    turn_id: str | None = None
    parent_id: str | None = None

    def child(self, parent_id: str) -> "TraceContext":
        return TraceContext(self.team_id, self.run_id, self.turn_id, parent_id)


@contextmanager
def step(
    ctx: TraceContext,
    step_type: str,
    name: str,
    input: Any = None,
    trust_label: str = "TRUSTED",
    conn: psycopg.Connection | None = None,
) -> Iterator[Step]:
    """Records one action. Pass `conn` on the money path so the record shares the state change's transaction."""
    current = Step(new_id("st"))
    _insert(conn, current.id, ctx, step_type, name, input, trust_label)
    try:
        yield current
    except BaseException as exc:
        # A rolled-back shared transaction loses the start record, so write the failure separately.
        if conn is not None:
            with tx() as own:
                _insert(own, current.id, ctx, step_type, name, input, trust_label, on_conflict=True)
                _finish(own, current, "FAILED", f"{type(exc).__name__}: {exc}"[:1000])
        else:
            with tx() as own:
                _finish(own, current, "FAILED", f"{type(exc).__name__}: {exc}"[:1000])
        raise
    else:
        if conn is not None:
            _finish(conn, current, "COMPLETED", None)
        else:
            with tx() as own:
                _finish(own, current, "COMPLETED", None)


def _insert(conn, step_id, ctx, step_type, name, input, trust_label, on_conflict=False) -> None:
    sql = """
        INSERT INTO trace_steps (id, team_id, turn_id, run_id, parent_id, step_type, name, input, trust_label)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """ + (" ON CONFLICT (id) DO NOTHING" if on_conflict else "")
    params = (step_id, ctx.team_id, ctx.turn_id, ctx.run_id, ctx.parent_id, step_type, name, Jsonb(redact(input)), trust_label)
    if conn is not None:
        conn.execute(sql, params)
    else:
        with tx() as own:
            own.execute(sql, params)


def _finish(conn, current: Step, status: str, error: str | None) -> None:
    conn.execute(
        """
        UPDATE trace_steps SET status = %s, error = %s, output = %s, evidence_ids = %s, model = %s,
            thinking_level = %s, input_tokens = %s, output_tokens = %s, cost_micros = %s, ended_at = clock_timestamp()
        WHERE id = %s
        """,
        (
            status,
            error,
            Jsonb(redact(current.output)),
            Jsonb(current.evidence_ids),
            current.model,
            current.thinking_level,
            current.input_tokens,
            current.output_tokens,
            current.cost_micros,
            current.id,
        ),
    )


def audit(
    conn: psycopg.Connection,
    team_id: str,
    actor_type: str,
    actor_id: str | None,
    event_type: str,
    entity_type: str,
    entity_id: str,
    data: dict | None = None,
    trace_step_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (team_id, actor_type, actor_id, event_type, entity_type, entity_id, data, trace_step_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (team_id, actor_type, actor_id, event_type, entity_type, entity_id, Jsonb(redact(data or {})), trace_step_id),
    )

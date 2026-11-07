"""Starts and resumes Routine Runs. routine_runs.status is authoritative; checkpoints only track progress."""

import os

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.routines.registry import get_routine
from payoai.settings import schema
from payoai.trace import TraceContext, step

_saver: PostgresSaver | None = None
_graphs: dict = {}


class RoutineBusy(Exception):
    """This Chat already has a step running."""


class NotAllowed(Exception):
    pass


class InvalidAnswer(Exception):
    pass


def _configure(conn) -> None:
    conn.execute(f'SET search_path TO "{schema()}"')


def saver() -> PostgresSaver:
    global _saver
    if _saver is None:
        pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=3,
            configure=_configure,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
        _saver = PostgresSaver(pool)
        _saver.setup()
    return _saver


def _graph(routine: str):
    if routine not in _graphs:
        _graphs[routine] = get_routine(routine).build().compile(checkpointer=saver())
    return _graphs[routine]


def role_of(conn, team_id: str, user_id: str) -> str | None:
    row = fetch_one(conn, "SELECT role FROM memberships WHERE team_id = %s AND user_id = %s", (team_id, user_id))
    return row["role"] if row else None


def create_run(routine: str, team_id: str, inputs: dict, trigger_type: str, actor: str | None = None, chat_id: str | None = None, turn_id: str | None = None) -> str:
    """Registers the run straight away, with its inputs and trigger. This commits no money."""
    config = get_routine(routine)
    run_id = new_id("run")
    with tx() as conn:
        if actor and trigger_type in ("CHAT", "CONFIRMATION") and role_of(conn, team_id, actor) not in config.roles:
            raise NotAllowed(f"Your role can't start {config.name}.")
    try:
        with tx() as conn:
            conn.execute(
                """
                INSERT INTO routine_runs (id, team_id, routine, routine_version, chat_id, started_turn_id, trigger_type, triggered_by, inputs, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING')
                """,
                (run_id, team_id, routine, config.version, chat_id, turn_id, trigger_type, actor, Jsonb(inputs)),
            )
    except UniqueViolation as exc:
        raise RoutineBusy("Another step is still running in this chat.") from exc
    return run_id


def run(run_id: str) -> dict:
    with tx() as conn:
        row = fetch_one(conn, "SELECT * FROM routine_runs WHERE id = %s", (run_id,))
    state = {
        "run_id": run_id,
        "team_id": row["team_id"],
        "turn_id": row["started_turn_id"],
        "actor": row["triggered_by"],
        "inputs": row["inputs"],
        "answers": {},
        "checks": [],
    }
    return _drive(row, state)


def start(routine: str, team_id: str, inputs: dict, trigger_type: str, actor: str | None = None, chat_id: str | None = None, turn_id: str | None = None) -> dict:
    return run(create_run(routine, team_id, inputs, trigger_type, actor, chat_id, turn_id))


def answer(run_id: str, team_id: str, value, turn_id: str | None = None) -> dict:
    """Resumes a Waiting Run. Anything outside the allowed answers is rejected."""
    with tx() as conn:
        row = fetch_one(conn, "SELECT * FROM routine_runs WHERE id = %s AND team_id = %s", (run_id, team_id))
        pending = row and fetch_one(conn, "SELECT * FROM run_questions WHERE run_id = %s AND status = 'OPEN' ORDER BY created_at DESC LIMIT 1", (run_id,))
        if not pending:
            raise InvalidAnswer("This run isn't waiting for an answer.")
        allowed = [o["value"] for o in pending["options"]]
        if allowed and value not in allowed:
            raise InvalidAnswer(f"Allowed answers: {allowed}")
        conn.execute("UPDATE run_questions SET status = 'ANSWERED', answer = %s WHERE id = %s", (Jsonb(value), pending["id"]))
        conn.execute("UPDATE routine_runs SET status = 'RUNNING', updated_at = now() WHERE id = %s", (run_id,))
    return _drive(row, Command(resume=value))


def _drive(row: dict, payload) -> dict:
    run_id, team_id = row["id"], row["team_id"]
    graph = _graph(row["routine"])
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
    try:
        graph.invoke(payload, config)
    except Exception as exc:
        with tx() as conn:
            conn.execute("UPDATE routine_runs SET status = 'FAILED', error = %s, updated_at = now() WHERE id = %s", (f"{type(exc).__name__}: {exc}"[:1000], run_id))
        return {"run_id": run_id, "status": "FAILED", "error": str(exc)[:300]}
    snapshot = graph.get_state(config)
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]
    if interrupts:
        return _pause(run_id, team_id, row["started_turn_id"], interrupts[0].value)
    return _finish(run_id, snapshot.values)


def _pause(run_id: str, team_id: str, turn_id: str | None, pending: dict) -> dict:
    ctx = TraceContext(team_id, run_id=run_id, turn_id=turn_id)
    with step(ctx, "NODE", "Pause: Ask User", input=pending) as s:
        question_id = new_id("q")
        with tx() as conn:
            conn.execute(
                "INSERT INTO run_questions (id, team_id, run_id, field, prompt, options) VALUES (%s, %s, %s, %s, %s, %s)",
                (question_id, team_id, run_id, pending["field"], pending["prompt"], Jsonb(pending["options"])),
            )
            conn.execute("UPDATE routine_runs SET status = 'WAITING_ANSWER', updated_at = now() WHERE id = %s", (run_id,))
        s.output = {"question_id": question_id}
    return {"run_id": run_id, "status": "WAITING_ANSWER", "question_id": question_id, "question": pending}


def _finish(run_id: str, values: dict) -> dict:
    result = {
        "decision": values.get("decision"),
        "reasons": values.get("reasons") or [],
        "findings": values.get("findings") or [],
        "explanation": values.get("explanation"),
        "stop": values.get("stop"),
        "obligation_id": values.get("obligation_id"),
        "intent_id": values.get("intent_id"),
        "approval_id": values.get("approval_id"),
        **(values.get("result") or {}),
    }
    status = "AWAITING_APPROVAL" if values.get("approval_id") else "COMPLETED"
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = %s, result = %s, updated_at = now() WHERE id = %s", (status, Jsonb(result), run_id))
    return {"run_id": run_id, "status": status, **result}

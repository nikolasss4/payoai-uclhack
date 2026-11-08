"""Starts and resumes Routine Runs. routine_runs.status is authoritative; checkpoints only track progress."""

import os

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.events import emit
from payoai.routines.registry import get_routine
from payoai.settings import schema
from payoai.trace import TraceContext, step

_saver: PostgresSaver | None = None
_graphs: dict = {}


class RoutineBusy(Exception):
    """This Chat already has a step running."""


class NotAllowed(Exception):
    pass


class AlreadyStarted(Exception):
    """This plan item already has its run, so a retried job doesn't start a second one."""


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


def create_run(routine: str, team_id: str, inputs: dict, trigger_type: str, actor: str | None = None, chat_id: str | None = None, turn_id: str | None = None, plan_item_id: str | None = None) -> str:
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
                INSERT INTO routine_runs (id, team_id, routine, routine_version, chat_id, started_turn_id, trigger_type, triggered_by, inputs, status, plan_item_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING', %s)
                """,
                (run_id, team_id, routine, config.version, chat_id, turn_id, trigger_type, actor, Jsonb(inputs), plan_item_id),
            )
    except UniqueViolation as exc:
        if exc.diag.constraint_name == "routine_runs_plan_item":
            raise AlreadyStarted("This plan item already has a run.") from exc
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


def start(routine: str, team_id: str, inputs: dict, trigger_type: str, actor: str | None = None, chat_id: str | None = None, turn_id: str | None = None, plan_item_id: str | None = None) -> dict:
    return run(create_run(routine, team_id, inputs, trigger_type, actor, chat_id, turn_id, plan_item_id))


def answer(run_id: str, team_id: str, value, turn_id: str | None = None) -> dict:
    """Resumes a Waiting Run. Anything outside the allowed answers is rejected."""
    with tx() as conn:
        row = fetch_one(conn, "SELECT * FROM routine_runs WHERE id = %s AND team_id = %s", (run_id, team_id))
        # Locked, so a double tap or repeated answer can't resume the run twice.
        pending = row and fetch_one(conn, "SELECT * FROM run_questions WHERE run_id = %s AND status = 'OPEN' ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (run_id,))
        if not pending:
            raise InvalidAnswer("This run isn't waiting for an answer.")
        allowed = [o["value"] for o in pending["options"]]
        if allowed and value not in allowed:
            raise InvalidAnswer(f"Allowed answers: {allowed}")
        conn.execute("UPDATE run_questions SET status = 'ANSWERED', answer = %s WHERE id = %s", (Jsonb(value), pending["id"]))
        try:
            conn.execute("UPDATE routine_runs SET status = 'RUNNING', updated_at = now() WHERE id = %s", (run_id,))
        except UniqueViolation as exc:
            raise RoutineBusy("Another step is still running in this chat.") from exc
    return _drive(row, Command(resume=value))


def _drive(row: dict, payload) -> dict:
    outcome = _drive_graph(row, payload)
    if row.get("plan_item_id"):
        from payoai.agent import plans  # Imported here, because plans starts runs through this module.

        plans.on_run_changed(row["plan_item_id"])
    return outcome


def _drive_graph(row: dict, payload) -> dict:
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
        outcome = _pause(run_id, team_id, row["started_turn_id"], interrupts[0].value)
        emit(team_id, row["chat_id"], "QUESTION", {"question_id": outcome["question_id"], **outcome["question"]}, turn_id=row["started_turn_id"], run_id=run_id)
        return outcome
    outcome = _finish(run_id, snapshot.values)
    _emit_outcome(team_id, row, outcome)
    return outcome


def _emit_outcome(team_id: str, row: dict, outcome: dict) -> None:
    run_id, chat_id, turn_id = row["id"], row["chat_id"], row["started_turn_id"]
    if outcome.get("approval_id"):
        emit(team_id, chat_id, "APPROVAL_CARD", {"approval_id": outcome["approval_id"]}, turn_id=turn_id, run_id=run_id)
    for item in outcome.get("items") or []:
        if item.get("approval_id"):
            emit(team_id, chat_id, "APPROVAL_CARD", {"approval_id": item["approval_id"]}, turn_id=turn_id, run_id=run_id)
    emit(team_id, chat_id, "STATUS", {"routine": row["routine"], "run_id": run_id, "finished": True, "status": outcome["status"], "decision": outcome.get("decision"), "stop": outcome.get("stop"), "explanation": outcome.get("explanation"), "counts": outcome.get("counts")}, turn_id=turn_id, run_id=run_id)


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
        "source_account_note": values.get("source_account_note"),
        **(values.get("result") or {}),
    }
    status = "AWAITING_APPROVAL" if values.get("approval_id") else "AWAITING_DOCUMENT" if result.get("awaiting_document") else "COMPLETED"
    with tx() as conn:
        conn.execute("UPDATE routine_runs SET status = %s, result = %s, updated_at = now() WHERE id = %s", (status, Jsonb(result), run_id))
    return {"run_id": run_id, "status": status, **result}

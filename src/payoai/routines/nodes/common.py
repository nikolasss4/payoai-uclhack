"""Node wrapper and shared state. Pauses stay out of wrapped Nodes because a resume restarts its Node."""

from functools import wraps
from operator import add
from typing import Annotated, TypedDict

from langgraph.types import interrupt

from payoai.db import Jsonb, new_id, tx
from payoai.domain.decide import POLICY_VERSION
from payoai.trace import TraceContext, step


def merge(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class RunState(TypedDict, total=False):
    run_id: str
    team_id: str
    turn_id: str | None
    actor: str | None
    inputs: dict
    answers: Annotated[dict, merge]
    pending_question: dict | None
    stop: str | None
    document_id: str | None
    extraction_id: str | None
    fields: dict
    failed_quotes: list
    counterparty_id: str | None
    obligation_id: str | None
    amount_minor: str | None
    asset: str | None
    destination_id: str | None
    source_account_id: str | None
    checks: Annotated[list, add]
    decision: str | None
    decision_id: str | None
    reasons: list
    findings: list
    explanation: str | None
    intent_id: str | None
    approval_id: str | None
    result: dict


def node(name: str, step_type: str = "NODE", trust_label: str = "TRUSTED"):
    """Records a Node as a Trace Step. If the record can't be saved, the Node doesn't run."""

    def decorate(fn):
        @wraps(fn)
        def wrapper(state: RunState) -> dict:
            ctx = TraceContext(state["team_id"], run_id=state["run_id"], turn_id=state.get("turn_id"))
            with step(ctx, step_type, name, input=_summary(state), trust_label=trust_label) as s:
                out = fn(state, ctx) or {}
                s.output = out
                return out

        wrapper.node_name = name
        return wrapper

    return decorate


def _summary(state: RunState) -> dict:
    keys = ("inputs", "answers", "document_id", "counterparty_id", "obligation_id", "destination_id", "source_account_id", "amount_minor", "asset", "decision")
    return {k: state.get(k) for k in keys if state.get(k) is not None}


def question(field: str, prompt: str, options: list[dict]) -> dict:
    return {"pending_question": {"field": field, "prompt": prompt, "options": options}}


def ask_user(state: RunState) -> dict:
    """The pause Node. It has no side effects, so restarting on resume is harmless."""
    pending = state["pending_question"]
    answer = interrupt(pending)
    return {"answers": {pending["field"]: answer}, "pending_question": None}


def record_check(state: RunState, check_name: str, rule_id: str, outcome: str, reason: str, evidence: dict | None = None) -> dict:
    check_id = new_id("chk")
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO check_results (id, team_id, run_id, obligation_id, check_name, rule_id, outcome, reason, evidence, policy_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (check_id, state["team_id"], state["run_id"], state.get("obligation_id"), check_name, rule_id, outcome, reason, Jsonb(evidence or {}), POLICY_VERSION),
        )
    return {"id": check_id, "check_name": check_name, "rule_id": rule_id, "outcome": outcome, "reason": reason}

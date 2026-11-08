"""Runs the Main Loop and Routines. It holds the Gemini key and no payment secrets."""

from payoai.settings import load_env

load_env(".env.shared", ".env.agent")

from payoai.agent import main_loop, plans  # noqa: E402
from payoai.db import tx  # noqa: E402
from payoai.memory import learner  # noqa: E402
from payoai.queue import enqueue  # noqa: E402
from payoai.routines import accounting, engine  # noqa: E402
from payoai.workers.loop import run_forever  # noqa: E402


def handle_turn(job: dict) -> None:
    payload = job["payload"]
    main_loop.run_turn(job["team_id"], payload["user_id"], payload["chat_id"], payload["turn_id"])
    with tx() as conn:
        enqueue(conn, "learn_memory", job["team_id"], {"turn_id": payload["turn_id"], "user_id": payload["user_id"]}, dedupe_key=f"learn:{payload['turn_id']}")


def handle_routine(job: dict) -> None:
    p = job["payload"]
    try:
        engine.start(p["routine"], job["team_id"], p["inputs"], "CONFIRMATION", actor=p.get("actor"), chat_id=p.get("chat_id"), turn_id=p.get("turn_id"), plan_item_id=p.get("plan_item_id"))
    except engine.AlreadyStarted:
        pass  # A retried job for a plan item that already has its run.


def handle_plan(job: dict) -> None:
    plans.advance(job["payload"]["plan_id"])


def handle_occurrence(job: dict) -> None:
    p = job["payload"]
    engine.start("prepare_occurrence", job["team_id"], {"mandate_id": p["mandate_id"], "period": p["period"], "late": p.get("late", False)}, "TIMER")


def handle_cash(job: dict) -> None:
    engine.start("cash_analysis", job["team_id"], {"days": 7}, "TIMER")


def handle_category(job: dict) -> None:
    accounting.suggest_category(job["team_id"], job["payload"]["expense_id"])


def handle_evidence(job: dict) -> None:
    p = job["payload"]
    accounting.attach_evidence(job["team_id"], p["user_id"], p["expense_id"], p["document_id"])


def handle_learn(job: dict) -> None:
    p = job["payload"]
    learner.learn_from_turn(job["team_id"], p["user_id"], p["turn_id"])


def handle_eval(job: dict) -> None:
    from evals.runner import run_saved

    run_saved(job["payload"]["eval_run_id"])


HANDLERS = {
    "run_turn": handle_turn,
    "run_routine": handle_routine,
    "advance_plan": handle_plan,
    "fire_occurrence": handle_occurrence,
    "cash_analysis": handle_cash,
    "suggest_category": handle_category,
    "attach_evidence": handle_evidence,
    "learn_memory": handle_learn,
    "run_eval": handle_eval,
}

if __name__ == "__main__":
    run_forever("agent-worker", HANDLERS)

"""Plans: the Main Loop's way to do a request that needs several Routine runs, such as ten bills.

The model makes the plan once, with every item's inputs validated then. After that, code runs it:
one advance_plan job starts one item and queues the next, so no job runs long and the model's
step budget doesn't apply. The queue never runs two jobs of one chat at once (see queue.claim),
so a person's message is answered between items, never alongside one.

An item that pauses for a person (a start card, a question, an approval) doesn't hold the plan
up; the next item starts. An item with depends_on waits until those items have gone through,
and is blocked if one of them didn't. The run or start card behind an item is the source of
truth for what it shows, so the plan card stays right after the plan itself has finished.
"""

import traceback
from datetime import datetime, timedelta, timezone

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.events import emit
from payoai.queue import enqueue
from payoai.routines.registry import ROUTINES, get_routine

MAX_ITEMS = 25
BUSY_RETRY_SECONDS = 5

# What a started item shows, from its run's status.
RUN_STATES = {
    "RUNNING": "RUNNING",
    "WAITING_ANSWER": "NEEDS_ANSWER",
    "AWAITING_APPROVAL": "NEEDS_APPROVAL",
    "AWAITING_DOCUMENT": "NEEDS_DOCUMENT",
    "COMPLETED": "DONE",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
}
CARD_STATES = {"PENDING": "NEEDS_START", "CONFIRMED": "RUNNING", "DECLINED": "DECLINED", "EXPIRED": "DECLINED"}
WENT_THROUGH = {"DONE", "NEEDS_APPROVAL", "NEEDS_DOCUMENT"}
DIDNT_GO_THROUGH = {"FAILED", "BLOCKED", "CANCELLED", "DECLINED"}
NEEDS_PERSON = {"NEEDS_START", "NEEDS_ANSWER", "NEEDS_APPROVAL", "NEEDS_DOCUMENT"}


class PlanRefused(Exception):
    pass


# Making a plan


def create(team_id: str, user_id: str, chat_id: str, turn_id: str, title: str, items: list[dict]) -> dict:
    """Saves a plan whose items the caller has already validated, and queues its first step.

    Each item is {"description", "routine", "inputs", "depends_on": [step numbers, from 1]}.
    """
    if not items:
        raise PlanRefused("A plan needs at least one item.")
    if len(items) > MAX_ITEMS:
        raise PlanRefused(f"A plan can have at most {MAX_ITEMS} items. Split the request.")
    for position, item in enumerate(items):
        for dep in item.get("depends_on") or []:
            if not isinstance(dep, int) or not 1 <= dep <= position:
                raise PlanRefused(f"Step {position + 1} can only depend on earlier steps.")
    plan_id = new_id("plan")
    with tx() as conn:
        if fetch_one(conn, "SELECT id FROM plans WHERE chat_id = %s AND status = 'RUNNING'", (chat_id,)):
            raise PlanRefused("A plan is still running in this chat. Wait for it, or stop it first.")
        conn.execute(
            "INSERT INTO plans (id, team_id, chat_id, turn_id, user_id, title) VALUES (%s, %s, %s, %s, %s, %s)",
            (plan_id, team_id, chat_id, turn_id, user_id, (title or "Plan").strip()[:120] or "Plan"),
        )
        for position, item in enumerate(items):
            conn.execute(
                "INSERT INTO plan_items (id, plan_id, team_id, position, description, routine, inputs, depends_on) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id("pli"), plan_id, team_id, position, str(item.get("description") or item["routine"])[:300], item["routine"], Jsonb(item["inputs"]), [d - 1 for d in item.get("depends_on") or []]),
            )
        _wake(conn, plan_id, team_id, chat_id)
    _emit(plan_id)
    return {"plan_id": plan_id, "items": len(items)}


# Running a plan


def advance(plan_id: str) -> None:
    """One step: blocks what can no longer go through, then starts the next item that can start."""
    with tx() as conn:
        plan = fetch_one(conn, "SELECT * FROM plans WHERE id = %s FOR UPDATE", (plan_id,))
        if plan is None or plan["status"] != "RUNNING":
            return
        rows = _rows(conn, plan_id)
        states = {r["position"]: _state(r) for r in rows}
        nxt = None
        for row in rows:
            # A claimed item from a job that crashed is picked up again before anything new.
            if row["status"] == "STARTING":
                nxt = row
                break
        for row in rows:
            if row["status"] != "PENDING":
                continue
            deps = [states[d] for d in row["depends_on"]]
            failed = [d + 1 for d, s in zip(row["depends_on"], deps) if s in DIDNT_GO_THROUGH or _blocked_decision(rows, d)]
            if failed:
                note = f"Step {failed[0]} didn't go through, so this one wasn't started."
                conn.execute("UPDATE plan_items SET status = 'BLOCKED', note = %s, updated_at = now() WHERE id = %s", (note, row["id"]))
                states[row["position"]] = "BLOCKED"
                continue
            if nxt is None and all(s in WENT_THROUGH and not _blocked_decision(rows, d) for d, s in zip(row["depends_on"], deps)):
                nxt = row
        finished = False
        if nxt is not None and nxt["status"] == "PENDING":
            conn.execute("UPDATE plan_items SET status = 'STARTING', updated_at = now() WHERE id = %s", (nxt["id"],))
        elif nxt is None and not any(s == "PENDING" for s in states.values()):
            conn.execute("UPDATE plans SET status = 'FINISHED', updated_at = now() WHERE id = %s", (plan_id,))
            finished = True
        # Otherwise every item left waits on one that needs a person. That item's run wakes the plan.
    if nxt is not None:
        _emit(plan_id)
        try:
            outcome = _start_item(plan, nxt)
        except Exception as exc:  # One broken item mustn't stall the rest. A failed job isn't retried.
            traceback.print_exc()
            outcome = "FAILED"
            with tx() as conn:
                conn.execute("UPDATE plan_items SET status = 'FAILED', note = %s, updated_at = now() WHERE id = %s AND status = 'STARTING'", (f"Couldn't start: {type(exc).__name__}", nxt["id"]))
        with tx() as conn:
            delay = BUSY_RETRY_SECONDS if outcome == "BUSY" else None
            _wake(conn, plan_id, plan["team_id"], plan["chat_id"], delay=delay)
    _emit(plan_id)
    if finished:
        _say_finished(plan)


def _start_item(plan: dict, item: dict) -> str:
    from payoai.routines import engine  # Only workers start runs, so the API doesn't load the graphs.

    config = get_routine(item["routine"])
    with tx() as conn:
        # A retried job finds the run or card it already made, and doesn't make another.
        if fetch_one(conn, "SELECT id FROM routine_runs WHERE plan_item_id = %s UNION ALL SELECT id FROM start_confirmations WHERE plan_item_id = %s", (item["id"], item["id"])):
            _mark(conn, item["id"], "STARTED")
            return "STARTED"
        if engine.role_of(conn, plan["team_id"], plan["user_id"]) not in config.roles:
            _mark(conn, item["id"], "FAILED", f"Your role can't start {config.name}.")
            return "FAILED"
    if config.start_confirmation:
        confirmation_id = new_id("cnf")
        with tx() as conn:
            made = conn.execute(
                """
                INSERT INTO start_confirmations (id, team_id, chat_id, turn_id, routine, inputs, requested_by, plan_item_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (plan_item_id) WHERE plan_item_id IS NOT NULL DO NOTHING RETURNING id
                """,
                (confirmation_id, plan["team_id"], plan["chat_id"], plan["turn_id"], item["routine"], Jsonb(item["inputs"]), plan["user_id"], item["id"]),
            ).fetchone()
            _mark(conn, item["id"], "STARTED")
        if made:
            emit(plan["team_id"], plan["chat_id"], "START_CARD", {"confirmation_id": confirmation_id, "routine": config.name, "inputs": item["inputs"], "plan_id": plan["id"]}, turn_id=plan["turn_id"])
        return "STARTED"
    try:
        engine.start(item["routine"], plan["team_id"], item["inputs"], "CHAT", actor=plan["user_id"], chat_id=plan["chat_id"], turn_id=plan["turn_id"], plan_item_id=item["id"])
    except engine.AlreadyStarted:
        pass
    except engine.RoutineBusy:
        # Another run in this chat holds the slot, such as one a person just tapped. Try again shortly.
        with tx() as conn:
            conn.execute("UPDATE plan_items SET status = 'PENDING', updated_at = now() WHERE id = %s AND status = 'STARTING'", (item["id"],))
        return "BUSY"
    except engine.NotAllowed as exc:
        with tx() as conn:
            _mark(conn, item["id"], "FAILED", str(exc))
        return "FAILED"
    with tx() as conn:
        _mark(conn, item["id"], "STARTED")
    return "STARTED"


def _mark(conn, item_id: str, status: str, note: str | None = None) -> None:
    conn.execute("UPDATE plan_items SET status = %s, note = COALESCE(%s, note), updated_at = now() WHERE id = %s", (status, note, item_id))


def _wake(conn, plan_id: str, team_id: str, chat_id: str, delay: int | None = None) -> None:
    """Queues the next step unless one is already queued, so a plan only ever has one chain of jobs."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (plan_id,))
    if fetch_one(conn, "SELECT id FROM jobs WHERE kind = 'advance_plan' AND status = 'QUEUED' AND payload->>'plan_id' = %s", (plan_id,)):
        return
    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay) if delay else None
    enqueue(conn, "advance_plan", team_id, {"plan_id": plan_id, "chat_id": chat_id}, run_at=run_at)


# Changes from outside the plan


def on_run_changed(plan_item_id: str) -> None:
    """Called by the engine whenever a plan item's run finishes or pauses. It never raises."""
    try:
        with tx() as conn:
            row = fetch_one(conn, "SELECT i.plan_id, p.team_id, p.chat_id, p.status FROM plan_items i JOIN plans p ON p.id = i.plan_id WHERE i.id = %s", (plan_item_id,))
            if row is None:
                return
            conn.execute("UPDATE plan_items SET status = 'STARTED', updated_at = now() WHERE id = %s AND status = 'STARTING'", (plan_item_id,))
            if row["status"] == "RUNNING":
                _wake(conn, row["plan_id"], row["team_id"], row["chat_id"])
        _emit(row["plan_id"])
    except Exception:  # The run's own outcome matters more than the plan card.
        traceback.print_exc()


def on_confirmation_decided(confirmation_id: str) -> None:
    """A declined start card means its item didn't go through, and items after it may be blocked."""
    with tx() as conn:
        row = fetch_one(
            conn,
            "SELECT i.id FROM start_confirmations c JOIN plan_items i ON i.id = c.plan_item_id WHERE c.id = %s",
            (confirmation_id,),
        )
    if row:
        on_run_changed(row["id"])


def stop(plan_id: str, team_id: str, user_id: str) -> dict:
    """Cancels the items not started yet. Started ones carry on, and their cards still need a tap."""
    with tx() as conn:
        plan = fetch_one(conn, "SELECT p.* FROM plans p JOIN chats c ON c.id = p.chat_id WHERE p.id = %s AND p.team_id = %s AND c.user_id = %s FOR UPDATE OF p", (plan_id, team_id, user_id))
        if plan is None:
            return {"status": "NOT_FOUND"}
        if plan["status"] != "RUNNING":
            return {"status": plan["status"]}
        conn.execute("UPDATE plans SET status = 'STOPPED', updated_at = now() WHERE id = %s", (plan_id,))
        cancelled = conn.execute("UPDATE plan_items SET status = 'CANCELLED', note = 'Stopped before it started.', updated_at = now() WHERE plan_id = %s AND status = 'PENDING'", (plan_id,)).rowcount
    _emit(plan_id)
    return {"status": "STOPPED", "cancelled": cancelled}


def confirm_all(plan_id: str, team_id: str, user_id: str, role: str) -> list[str]:
    """The person taps once for every start card of this plan that is waiting on them."""
    with tx() as conn:
        rows = fetch_all(
            conn,
            """
            UPDATE start_confirmations c SET status = 'CONFIRMED'
            FROM plan_items i WHERE i.id = c.plan_item_id AND i.plan_id = %s AND c.team_id = %s AND c.requested_by = %s AND c.status = 'PENDING'
            RETURNING c.*
            """,
            (plan_id, team_id, user_id),
        )
        started = []
        for row in rows:
            if role not in get_routine(row["routine"]).roles:
                conn.execute("UPDATE start_confirmations SET status = 'PENDING' WHERE id = %s", (row["id"],))
                continue
            enqueue(conn, "run_routine", team_id, routine_job(row, user_id), dedupe_key=f"confirm:{row['id']}")
            started.append(row["id"])
    _emit(plan_id)
    return started


def routine_job(confirmation: dict, user_id: str) -> dict:
    return {
        "routine": confirmation["routine"], "inputs": confirmation["inputs"], "actor": user_id, "chat_id": confirmation["chat_id"],
        "turn_id": confirmation["turn_id"], "confirmation_id": confirmation["id"], "plan_item_id": confirmation.get("plan_item_id"),
    }


# Reading a plan


def _rows(conn, plan_id: str) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT i.*, r.id AS run_id, r.status AS run_status, r.updated_at AS run_updated_at, r.result->>'decision' AS decision,
               r.result->>'obligation_id' AS bill_id, r.result->>'intent_id' AS payment_id,
               c.id AS confirmation_id, c.status AS confirmation_status, pi.status AS payment_status
        FROM plan_items i
        LEFT JOIN routine_runs r ON r.plan_item_id = i.id
        LEFT JOIN start_confirmations c ON c.plan_item_id = i.id
        LEFT JOIN payment_intents pi ON pi.id = r.result->>'intent_id'
        WHERE i.plan_id = %s ORDER BY i.position
        """,
        (plan_id,),
    )


def _state(row: dict) -> str:
    if row["status"] in ("PENDING", "FAILED", "BLOCKED", "CANCELLED"):
        return row["status"]
    if row["run_status"]:
        return RUN_STATES.get(row["run_status"], "RUNNING")
    if row["confirmation_status"]:
        return CARD_STATES.get(row["confirmation_status"], "RUNNING")
    return "RUNNING"


def _blocked_decision(rows: list[dict], position: int) -> bool:
    """A run can complete and still be stopped by its checks. Items after it shouldn't build on it."""
    return next((r["decision"] == "BLOCK" for r in rows if r["position"] == position), False)


def snapshot(plan_id: str, team_id: str | None = None) -> dict | None:
    with tx() as conn:
        plan = fetch_one(conn, "SELECT * FROM plans WHERE id = %s AND (%s::text IS NULL OR team_id = %s)", (plan_id, team_id, team_id))
        if plan is None:
            return None
        rows = _rows(conn, plan_id)
    items = []
    for row in rows:
        state = _state(row)
        waiting_on = [d + 1 for d in row["depends_on"]] if state == "PENDING" and row["depends_on"] else []
        items.append({
            "step": row["position"] + 1, "description": row["description"], "routine": row["routine"],
            "routine_name": ROUTINES[row["routine"]].name if row["routine"] in ROUTINES else row["routine"],
            "state": state, "decision": row["decision"], "note": row["note"], "depends_on": [d + 1 for d in row["depends_on"]], "waiting_on": waiting_on,
            "run_id": row["run_id"], "confirmation_id": row["confirmation_id"], "bill_id": row["bill_id"], "payment_id": row["payment_id"], "payment_status": row["payment_status"],
        })
    counts = {
        "total": len(items),
        "went_through": sum(1 for i in items if i["state"] in WENT_THROUGH and i["decision"] != "BLOCK"),
        "needs_you": sum(1 for i in items if i["state"] in NEEDS_PERSON),
        "didnt_go_through": sum(1 for i in items if i["state"] in DIDNT_GO_THROUGH or i["decision"] == "BLOCK"),
        "not_started": sum(1 for i in items if i["state"] == "PENDING"),
    }
    counts["settled"] = counts["total"] - counts["not_started"] - sum(1 for i in items if i["state"] == "RUNNING")
    # The newest change anywhere in the plan, so the app can tell which of two copies is current.
    changed = max([plan["updated_at"], *(r["updated_at"] for r in rows), *(r["run_updated_at"] for r in rows if r["run_updated_at"])])
    return {"id": plan["id"], "title": plan["title"], "status": plan["status"], "turn_id": plan["turn_id"], "items": items, "counts": counts, "updated_at": changed.isoformat()}


def latest_for_chat(chat_id: str) -> dict | None:
    """The plan the Main Loop sees in its context: the running one, or else the newest."""
    with tx() as conn:
        row = fetch_one(conn, "SELECT id FROM plans WHERE chat_id = %s ORDER BY (status = 'RUNNING') DESC, created_at DESC LIMIT 1", (chat_id,))
    return snapshot(row["id"]) if row else None


def _emit(plan_id: str) -> None:
    snap = snapshot(plan_id)
    if snap:
        with tx() as conn:
            plan = fetch_one(conn, "SELECT team_id, chat_id FROM plans WHERE id = %s", (plan_id,))
        emit(plan["team_id"], plan["chat_id"], "PLAN", snap)


def _say_finished(plan: dict) -> None:
    """Written by code from the records, so it can't overstate what happened."""
    snap = snapshot(plan["id"])
    if not snap:
        return
    c = snap["counts"]
    parts = []
    if c["went_through"]:
        parts.append(f"{c['went_through']} went through")
    if c["needs_you"]:
        parts.append(f"{c['needs_you']} need{'s' if c['needs_you'] == 1 else ''} you to tap a card or answer")
    if c["didnt_go_through"]:
        parts.append(f"{c['didnt_go_through']} didn't go through")
    text = f"Every step of “{snap['title']}” has been started: " + ", ".join(parts) + "." if parts else f"“{snap['title']}” is finished."
    emit(plan["team_id"], plan["chat_id"], "SUGGESTION", {"text": text, "plan_id": plan["id"]})

"""Memory. It can influence suggestions but never a decision, and check Nodes never receive it."""

import re

from payoai.db import Jsonb, fetch_all, fetch_one, new_id, tx
from payoai.trace import TraceContext, step

ALLOWED = {"merchant_preference", "category_hint", "source_account_hint", "purchase_constraint", "reply_style", "business_context"}
AUTO_ALLOWED = ALLOWED - {"business_context"}
FORBIDDEN = re.compile(r"(\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b|0x[a-fA-F0-9]{40}|\b\d{8}\b|\b\d{2}-\d{2}-\d{2}\b|api[_ ]?key|password|secret)", re.I)
ABOUT_CHECKS = re.compile(r"\b(skip|ignore|bypass|disable|no need)\b.*\b(check|review|approval|verification|verify)\b|\btrusted\b|\bsafe\b|\bnormal amount\b", re.I)
OBSERVED_THRESHOLD = 3
MAX_PREFERENCES = 15


class MemoryRefused(ValueError):
    pass


def validate(memory_type: str, value: dict) -> None:
    if memory_type not in ALLOWED:
        raise MemoryRefused(f"unknown memory type {memory_type}")
    text = str(value)
    if FORBIDDEN.search(text):
        raise MemoryRefused("Memory can't hold account numbers, addresses or keys.")
    if ABOUT_CHECKS.search(text):
        raise MemoryRefused("Memory can't change checks, or say a recipient is safe or trusted.")


def save(team_id: str, user_id: str | None, memory_type: str, subject: str | None, value: dict, origin: str, source_ids: list[str], evidence_count: int = 1, ctx: TraceContext | None = None) -> str:
    validate(memory_type, value)
    memory_id = new_id("mem")

    def write():
        with tx() as conn:
            current = fetch_one(
                conn,
                "SELECT id, version FROM memories WHERE team_id = %s AND type = %s AND subject IS NOT DISTINCT FROM %s AND user_id IS NOT DISTINCT FROM %s AND status = 'ACTIVE'",
                (team_id, memory_type, subject, user_id),
            )
            version = 1
            if current:
                conn.execute("UPDATE memories SET status = 'SUPERSEDED' WHERE id = %s", (current["id"],))
                version = current["version"] + 1
            conn.execute(
                "INSERT INTO memories (id, team_id, user_id, type, subject, value, origin, source_ids, evidence_count, version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (memory_id, team_id, user_id, memory_type, subject, Jsonb(value), origin, Jsonb(source_ids), evidence_count, version),
            )

    if ctx is None:
        write()
    else:
        with step(ctx, "MEMORY_WRITE", f"Save {memory_type}", input={"value": value, "origin": origin}) as s:
            write()
            s.output = {"memory_id": memory_id}
    return memory_id


def preferences_for(team_id: str, user_id: str | None, types: tuple[str, ...] | None = None, subject: str | None = None) -> list[dict]:
    """Direct lookup by type and subject. Each consumer asks only for what it needs."""
    with tx() as conn:
        rows = fetch_all(
            conn,
            """
            SELECT id, type, subject, value, origin FROM memories
            WHERE team_id = %s AND status = 'ACTIVE' AND (user_id IS NULL OR user_id = %s)
              AND (%s::text[] IS NULL OR type = ANY(%s)) AND (%s::text IS NULL OR subject = %s)
            ORDER BY user_id IS NULL, created_at DESC LIMIT %s
            """,
            (team_id, user_id, list(types) if types else None, list(types) if types else None, subject, subject, MAX_PREFERENCES),
        )
    return rows


def record_observation(team_id: str, user_id: str, memory_type: str, subject: str, value: dict, event_id: str) -> str | None:
    """Repeated actions: code counts them and saves after 3 consistent ones. Only runs in Auto Mode.

    Acting against a memory resets its count at once, and it stays until the new behaviour has
    repeated enough to replace it. Actions from before the latest saved or undone memory don't count.
    """
    if not auto_mode(team_id) or memory_type not in AUTO_ALLOWED:
        return None
    with tx() as conn:
        active = fetch_one(
            conn,
            "SELECT id, value FROM memories WHERE team_id = %s AND type = %s AND subject IS NOT DISTINCT FROM %s AND user_id = %s AND status = 'ACTIVE'",
            (team_id, memory_type, subject, user_id),
        )
        if active and active["value"] == value:
            conn.execute("UPDATE memories SET evidence_count = evidence_count + 1 WHERE id = %s", (active["id"],))
            return active["id"]
        if active:
            conn.execute("UPDATE memories SET evidence_count = 0 WHERE id = %s", (active["id"],))
        since = fetch_one(conn, "SELECT max(created_at) AS at FROM memories WHERE team_id = %s AND type = %s AND subject IS NOT DISTINCT FROM %s AND user_id = %s", (team_id, memory_type, subject, user_id))["at"]
        recent = fetch_all(
            conn,
            """
            SELECT data FROM audit_events
            WHERE team_id = %s AND actor_id = %s AND event_type = 'MemoryObservation' AND entity_type = %s AND entity_id = %s
              AND (%s::timestamptz IS NULL OR created_at > %s)
            ORDER BY id DESC LIMIT 50
            """,
            (team_id, user_id, memory_type, subject, since, since),
        )
    # The unbroken run of this value, counting each record once, so re-saving one expense isn't three actions.
    sources = set()
    for row in recent:
        if row["data"].get("value") != value:
            break
        sources.add(row["data"].get("source") or row["data"].get("id"))
    if len(sources) < OBSERVED_THRESHOLD:
        return None
    return save(team_id, user_id, memory_type, subject, value, "observed", [event_id, *sorted(s for s in sources if s)], evidence_count=len(sources))


def auto_mode(team_id: str) -> bool:
    with tx() as conn:
        row = fetch_one(conn, "SELECT auto_mode FROM team_controls WHERE team_id = %s", (team_id,))
    return bool(row and row["auto_mode"])

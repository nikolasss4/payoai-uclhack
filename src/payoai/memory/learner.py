"""Auto Mode. Learns only from the user's own messages, never documents, tools or Main Loop replies."""

from payoai.agent import llm
from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.memory import AUTO_ALLOWED, MemoryRefused, auto_mode, record_observation, save
from payoai.trace import TraceContext, audit

MAX_MESSAGE_CHARS = 500
LEARNER_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(AUTO_ALLOWED)},
                    "subject": {"type": ["string", "null"]},
                    "prefer": {"type": "string", "enum": ["uk_retailers", "returns_policy", "short_replies", "detailed_replies", "lowest_price", "fastest_delivery"]},
                },
                "required": ["type", "prefer"],
            },
        }
    },
    "required": ["candidates"],
}
PROMPT = (
    "Does this message from a user state a lasting preference? Only report an explicit, lasting preference such as 'always use UK retailers'. "
    "A one-off request isn't a preference. If there's none, return an empty list. Message: "
)


def learn_from_turn(team_id: str, user_id: str, turn_id: str) -> list[str]:
    """A long pasted block is skipped, because it may be a document rather than the user's own words."""
    if not auto_mode(team_id):
        return []
    with tx() as conn:
        turn = fetch_one(conn, "SELECT user_input FROM turns WHERE id = %s AND team_id = %s", (turn_id, team_id))
    text = (turn["user_input"] if turn else "").split("\n[The user attached document_id")[0]
    if not text or len(text) > MAX_MESSAGE_CHARS:
        return []
    try:
        data = llm.call("memory_learner", [llm.text_part(PROMPT + text)], TraceContext(team_id, turn_id=turn_id), schema=LEARNER_SCHEMA).data
    except llm.LLMFailure:
        return []
    saved = []
    for candidate in data["candidates"]:
        # The schema limits this too, but model output never decides what may be stored.
        if candidate.get("type") not in AUTO_ALLOWED:
            continue
        try:
            saved.append(save(team_id, user_id, candidate["type"], candidate.get("subject"), {"prefer": candidate["prefer"]}, "stated", [turn_id], ctx=TraceContext(team_id, turn_id=turn_id)))
        except MemoryRefused:
            continue
    return saved


def observe(team_id: str, user_id: str | None, memory_type: str, subject: str, value: dict, source_id: str | None = None) -> str | None:
    """Records a typed action by a person, so code can count repeats. Only used in Auto Mode.

    Returns the memory this action saved or strengthened, if any. Documents never call this.
    """
    if not user_id or user_id in ("SYSTEM", "AGENT") or memory_type not in AUTO_ALLOWED or not auto_mode(team_id):
        return None
    event_id = new_id("obs")
    with tx() as conn:
        audit(conn, team_id, "USER", user_id, "MemoryObservation", memory_type, subject, {"value": value, "id": event_id, "source": source_id})
    return record_observation(team_id, user_id, memory_type, subject, value, event_id)


__all__ = ["Jsonb"]

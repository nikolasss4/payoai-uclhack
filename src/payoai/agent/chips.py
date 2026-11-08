"""Chips and the reply check. Code displays money facts, and the model writes the words around them."""

import json
import re

from payoai.agent import read_tools

CITATION = re.compile(r"\[\[(payment|bill|expense|account|run):([A-Za-z0-9_]+)\]\]")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
PAID_WORDS = re.compile(r"\b(paid|sent|settled|confirmed|completed|arrived|received)\b", re.I)
NEGATION = re.compile(r"\b(not|n't|no|hasn't|haven't|unsure|unknown|whether|can't say|cannot say|yet)\b", re.I)
MONEY = re.compile(r"[£$€]\s?\d|\b\d[\d,]*(?:\.\d+)?\s?(USDC|GBP|USD|EUR)\b")
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")


def lookup(team_id: str, kind: str, record_id: str) -> dict | None:
    if kind == "payment":
        return read_tools.get_payment(team_id, record_id)
    if kind == "bill":
        return read_tools.get_bill(team_id, record_id)
    if kind == "expense":
        return read_tools.get_expense(team_id, record_id)
    if kind == "account":
        return next((a for a in read_tools.get_balances(team_id) if a["id"] == record_id), None)
    if kind == "run":
        return read_tools.get_run(team_id, record_id)
    return None


def render(team_id: str, text: str) -> list[dict]:
    """Splits a reply into text and Chips. Chips read the live record every time they're shown."""
    parts, position = [], 0
    for match in CITATION.finditer(text):
        if match.start() > position:
            parts.append({"type": "text", "text": text[position:match.start()]})
        parts.append(_chip(team_id, match.group(1), match.group(2)))
        position = match.end()
    if position < len(text):
        parts.append({"type": "text", "text": text[position:]})
    return parts


def _chip(team_id: str, kind: str, record_id: str) -> dict:
    record = lookup(team_id, kind, record_id)
    if record is None:
        return {"type": "chip", "kind": kind, "id": record_id, "missing": True, "label": "record not found"}
    amount = record.get("amount") or record.get("gross") or record.get("balance")
    status = record.get("status") or ("stale balance" if record.get("stale") else None)
    return {"type": "chip", "kind": kind, "id": record_id, "amount": amount, "status": status, "label": record.get("counterparty") or record.get("name") or record.get("routine")}


def check_reply(team_id: str, text: str, tool_results: list) -> list[str]:
    """Returns problems. It can't catch every wording issue, so the LLM judge also scores truthfulness."""
    blob = json.dumps(tool_results, default=str).replace(",", "")
    plain = CITATION.sub(" ", text)
    problems = []
    for number in NUMBER.findall(plain):
        cleaned = number.replace(",", "")
        if "." not in cleaned and len(cleaned) < 3:
            continue
        if cleaned not in blob:
            problems.append(f"The figure {number} isn't in this turn's tool results.")
    for sentence in SENTENCE.findall(text):
        if not PAID_WORDS.search(sentence) or NEGATION.search(sentence):
            continue
        cited = CITATION.findall(sentence)
        payments = [c for c in cited if c[0] in ("payment", "bill")]
        if not payments and (MONEY.search(sentence) or re.search(r"\b(payment|bill|invoice)\b", sentence, re.I)):
            problems.append(f"'{sentence.strip()}' claims an outcome with no citation.")
        for kind, record_id in payments:
            record = lookup(team_id, kind, record_id)
            status = (record or {}).get("status")
            if status not in ("CONFIRMED", "PAID", "MANUALLY_RESOLVED"):
                problems.append(f"'{sentence.strip()}' claims an outcome, but {record_id}'s status is {status}.")
    if MONEY.search(plain) and not CITATION.search(text) and tool_results:
        problems.append("The reply states money figures with no citation.")
    return problems


def raw_records(tool_results: list) -> str:
    """Shown when a reply fails the check twice: the records, with nothing added."""
    return "I can't state this reliably, so here are the records:\n" + json.dumps(tool_results, default=str, indent=1)[:3000]

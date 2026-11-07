"""Record Expense. An expense exists only after CONFIRMED. Code builds the amounts and journal preview."""

import psycopg

from payoai.db import Jsonb, fetch_one, new_id
from payoai.domain.money import to_minor
from payoai.queue import enqueue
from payoai.trace import audit


def record_expense(conn: psycopg.Connection, intent: dict, attempt: dict) -> str:
    obligation = fetch_one(
        conn,
        "SELECT o.*, e.fields, cp.classifications FROM obligations o LEFT JOIN extractions e ON e.id = o.extraction_id LEFT JOIN counterparties cp ON cp.id = o.counterparty_id WHERE o.id = %s",
        (intent["obligation_id"],),
    )
    if "internal" in (obligation["classifications"] or []):
        return ""  # A transfer between a team's own accounts is not an expense.
    team = intent["team_id"]
    existing = fetch_one(conn, "SELECT expense_id FROM expense_versions WHERE intent_id = %s LIMIT 1", (intent["id"],))
    if existing:
        return existing["expense_id"]
    fields = obligation["fields"] or {}
    asset = intent["asset"]
    gross, net, tax = int(intent["amount_minor"]), None, None
    has_invoice = obligation["document_id"] is not None
    if has_invoice and asset in ("GBP", "USD", "EUR"):
        net = _minor(fields.get("subtotal"), asset)
        tax = _minor(fields.get("vat_total"), asset)
    status = "SUPPORTED" if has_invoice else "MISSING_EVIDENCE"
    expense_id = new_id("exp")
    conn.execute(
        """
        INSERT INTO expense_versions (id, team_id, expense_id, version, obligation_id, intent_id, counterparty_id, business_purpose, accounting_period, asset, gross_minor, net_minor, tax_minor, gbp_value_minor, rate_source, status, reason, actor)
        VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Created from a confirmed payment', 'SYSTEM')
        """,
        (
            new_id("exv"), team, expense_id, obligation["id"], intent["id"], intent["counterparty_id"], obligation["description"],
            (obligation["period"] or attempt["updated_at"].strftime("%Y-%m")), asset, gross, net, tax,
            gross if asset == "GBP" else None, "same currency" if asset == "GBP" else None, status,
        ),
    )
    conn.execute(
        "INSERT INTO journal_proposals (id, team_id, expense_id, lines) VALUES (%s, %s, %s, %s)",
        (new_id("jnl"), team, expense_id, Jsonb(_journal(gross, net, tax, asset))),
    )
    audit(conn, team, "SYSTEM", None, "ExpenseCreated", "expense", expense_id, {"status": status, "intent_id": intent["id"]})
    enqueue(conn, "suggest_category", team, {"expense_id": expense_id}, dedupe_key=f"category:{expense_id}")
    if status == "MISSING_EVIDENCE":
        conn.execute(
            "INSERT INTO exceptions (id, team_id, expense_id, kind, details) VALUES (%s, %s, %s, 'MISSING_DOCUMENT', %s) ON CONFLICT DO NOTHING",
            (new_id("exc"), team, expense_id, Jsonb({"reason": "No invoice or receipt is attached to this payment."})),
        )
    return expense_id


def _minor(value, asset) -> int | None:
    try:
        return to_minor(value, asset) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _journal(gross, net, tax, asset) -> list[dict]:
    expense_amount = net if net is not None else gross
    lines = [{"account": "Expenses (uncategorised)", "debit_minor": expense_amount, "credit_minor": 0, "asset": asset}]
    if tax:
        lines.append({"account": "VAT input", "debit_minor": tax, "credit_minor": 0, "asset": asset})
    lines.append({"account": "Bank or wallet", "debit_minor": 0, "credit_minor": expense_amount + (tax or 0), "asset": asset})
    return lines

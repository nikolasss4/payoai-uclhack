"""UK invoice checks. Code picks the rules from extracted facts and checks them exactly.

Answers "is this document good enough for the books?", which is separate from "is it safe to pay?",
so these results can reach NEEDS_REVIEW or NEEDS_INFORMATION but never BLOCK.
Scope: UK domestic full and simplified VAT invoices, and invoices from suppliers not registered for VAT.
"""

import re

from payoai.domain.money import to_minor

RULESET_VERSION = "uk-invoice-1.1"
# VAT Notice 700/21 sections 4.1 and 4.5, last updated 18 March 2024, read on 7 November 2026.
SOURCE = "https://www.gov.uk/guidance/record-keeping-for-vat-notice-70021"
# The pages read state no rounding rule. One penny per line is our own versioned setting.
ROUNDING_TOLERANCE_PER_LINE_MINOR = 1
# The number's format only. No source was confirmed for this pattern, and nothing is checked with HMRC.
VAT_NUMBER = re.compile(r"^(GB)?\s*\d{3}\s*\d{4}\s*\d{2}(\s*\d{3})?$", re.I)
SIMPLIFIED_LIMIT_MINOR = 25000

FULL_VAT_FIELDS = {
    "invoice_number": "a unique invoice number",
    "supplier_name": "the supplier's name",
    "supplier_address": "the supplier's address",
    "vat_number": "the supplier's VAT registration number",
    "customer_name": "the customer's name",
    "customer_address": "the customer's address",
}
SIMPLIFIED_FIELDS = {
    "supplier_name": "the supplier's name",
    "supplier_address": "the supplier's address",
    "vat_number": "the supplier's VAT registration number",
}


def _money(fields: dict, name: str, asset: str) -> int | None:
    value = fields.get(name)
    if value in (None, ""):
        return None
    try:
        return to_minor(value, asset)
    except (ValueError, TypeError):
        return None


def result(rule_id: str, outcome: str, reason: str, **evidence) -> dict:
    return {"rule_id": rule_id, "outcome": outcome, "reason": reason, "evidence": {"source": SOURCE, **evidence}}


def classify(fields: dict) -> str:
    """FULL_VAT, NON_VAT, UNSUPPORTED or UNCLEAR, from facts on the invoice."""
    text = " ".join(str(fields.get(k) or "") for k in ("notes", "vat_treatment")).lower()
    country = (fields.get("supplier_country") or "GB").upper()
    if "reverse charge" in text or country not in ("GB", "UK", "UNITED KINGDOM"):
        return "UNSUPPORTED"
    if (fields.get("currency") or "GBP").upper() != "GBP":
        return "UNSUPPORTED"
    vat_total = _money(fields, "vat_total", "GBP")
    line_vat = any(_money(line, "vat", "GBP") for line in fields.get("line_items") or [])
    charged = bool(vat_total) or line_vat
    has_number = bool(fields.get("vat_number"))
    if charged:
        return "FULL_VAT"
    if not has_number:
        return "NON_VAT"
    return "UNCLEAR"


def evaluate(fields: dict) -> list[dict]:
    asset = (fields.get("currency") or "").upper()
    out: list[dict] = []
    if not asset or _money(fields, "total", asset if asset in ("GBP", "USD", "EUR", "USDC") else "GBP") is None:
        out.append(result("UK-ESSENTIAL-AMOUNT", "NEEDS_INFORMATION", "The invoice has no readable total or currency, so the amount to pay is unknown."))
    if not fields.get("due_date"):
        out.append(result("UK-ESSENTIAL-DUE-DATE", "NEEDS_INFORMATION", "The invoice has no due date."))

    kind = classify(fields)
    if kind == "UNSUPPORTED":
        out.append(result("UK-UNSUPPORTED-TYPE", "NEEDS_REVIEW", "This invoice type (foreign supplier, foreign currency or reverse charge) isn't checked by this ruleset, so no compliance result is given."))
        return out
    if kind == "UNCLEAR":
        out.append(result("UK-UNCLEAR-TYPE", "NEEDS_REVIEW", "The invoice shows a VAT number but charges no VAT, so its treatment is unclear and isn't assumed."))
    if kind == "NON_VAT":
        out.append(result("UK-VAT-FIELDS", "NOT_APPLICABLE", "No VAT is charged and no VAT number is shown, so VAT invoice fields don't apply."))
    if kind == "FULL_VAT":
        total = _money(fields, "total", "GBP")
        # At or below £250 including VAT, a simplified invoice is allowed, so fewer fields are required.
        simplified = total is not None and total <= SIMPLIFIED_LIMIT_MINOR
        required = SIMPLIFIED_FIELDS if simplified else FULL_VAT_FIELDS
        for name, label in required.items():
            if not fields.get(name):
                out.append(result(f"UK-VAT-FIELD-{name.upper()}", "NEEDS_REVIEW", f"VAT is charged, but the invoice is missing {label}, so the VAT may not be reclaimable.", field=name))
        if not (fields.get("supply_date") or fields.get("issue_date")):
            out.append(result("UK-VAT-FIELD-TIME_OF_SUPPLY", "NEEDS_REVIEW", "VAT is charged, but the invoice shows no time of supply or issue date, so the VAT may not be reclaimable."))
        number = fields.get("vat_number")
        if number and not VAT_NUMBER.match(number.strip()):
            out.append(result("UK-VAT-NUMBER-FORMAT", "NEEDS_REVIEW", "The VAT number isn't in a valid UK format. Its existence was not checked with HMRC.", value=number))
        if number and VAT_NUMBER.match(number.strip()):
            out.append(result("UK-VAT-NUMBER-LOOKUP", "NEEDS_REVIEW" if fields.get("require_vat_lookup") else "PASS", "The VAT number's format is valid. Its registration wasn't checked with HMRC."))
        for index, line in enumerate(fields.get("line_items") or []):
            if not line.get("description"):
                out.append(result("UK-VAT-LINE-DESCRIPTION", "NEEDS_REVIEW", f"Line {index + 1} has no description.", line=index))
            if not line.get("vat_rate"):
                out.append(result("UK-VAT-LINE-RATE", "NEEDS_REVIEW", f"Line {index + 1} doesn't show a VAT rate.", line=index))
            if not simplified and not (line.get("quantity") and line.get("unit_price")):
                out.append(result("UK-VAT-LINE-QUANTITY", "NEEDS_REVIEW", f"Line {index + 1} doesn't show both a quantity and a unit price, which a full VAT invoice needs.", line=index))
    out.extend(_arithmetic(fields, asset))
    if not any(r["outcome"] in ("NEEDS_REVIEW", "NEEDS_INFORMATION") for r in out):
        out.append(result("UK-DOCUMENT-CHECKS", "PASS", "All checks applicable to this invoice type passed."))
    return out


def _arithmetic(fields: dict, asset: str) -> list[dict]:
    if asset not in ("GBP", "USD", "EUR"):
        return []
    out = []
    subtotal = _money(fields, "subtotal", asset)
    vat_total = _money(fields, "vat_total", asset) or 0
    total = _money(fields, "total", asset)
    lines = fields.get("line_items") or []
    tolerance = ROUNDING_TOLERANCE_PER_LINE_MINOR * max(1, len(lines))
    if lines and subtotal is not None:
        nets = [_money(line, "net", asset) for line in lines]
        if all(n is not None for n in nets) and abs(sum(nets) - subtotal) > tolerance:
            out.append(result("UK-ARITH-LINES", "NEEDS_REVIEW", f"Line items add up to {sum(nets)} minor units, but the subtotal says {subtotal}.", lines_minor=sum(nets), subtotal_minor=subtotal))
    if subtotal is not None and total is not None and abs(subtotal + vat_total - total) > tolerance:
        out.append(result("UK-ARITH-TOTAL", "NEEDS_REVIEW", f"Subtotal plus VAT comes to {subtotal + vat_total} minor units, but the total says {total}. A person must choose which is right.", computed_minor=subtotal + vat_total, stated_minor=total))
    for index, line in enumerate(lines):
        net, vat, rate = _money(line, "net", asset), _money(line, "vat", asset), line.get("vat_rate")
        try:
            percent = float(str(rate).strip().rstrip("%")) if rate not in (None, "") else None
        except ValueError:
            percent = None
        if net is not None and vat is not None and percent is not None:
            expected = round(net * percent / 100)
            if abs(expected - vat) > ROUNDING_TOLERANCE_PER_LINE_MINOR:
                out.append(result("UK-ARITH-VAT-LINE", "NEEDS_REVIEW", f"Line {index + 1}'s VAT should be {expected} minor units at {percent}%, but it says {vat}.", line=index))
    return out

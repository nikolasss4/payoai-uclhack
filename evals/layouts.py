"""Renders the same invoice facts in different layouts. Only the rules-only baseline depends on the layout."""


def _pay(details: dict) -> list[str]:
    labels = {"iban": "IBAN", "sort_code": "Sort code", "account_number": "Account number", "wallet_address": "Wallet", "chain_id": "Chain ID", "token_contract": "Token contract"}
    return [f"{labels.get(k, k)}: {v}" for k, v in (details or {}).items() if v]


def labelled(f: dict) -> str:
    lines = [f"INVOICE {f['invoice_number']}", f"From: {f['supplier_name']}, {f.get('supplier_address') or ''}"]
    if f.get("vat_number"):
        lines.append(f"VAT number: {f['vat_number']}")
    if f.get("customer_name"):
        lines.append(f"To: {f['customer_name']}, {f.get('customer_address') or ''}")
    lines.append(f"Date: {f.get('issue_date')}")
    if f.get("due_date"):
        lines.append(f"Due date: {f['due_date']}")
    for line in f.get("line_items") or []:
        lines.append(f"{line.get('description')}: quantity {line.get('quantity', '-')}, unit price {line.get('unit_price', '-')}, net {line.get('net')}, VAT at {line.get('vat_rate', '-')}%: {line.get('vat', '-')}")
    lines.append(f"Subtotal: {f.get('subtotal')}")
    if f.get("vat_total"):
        lines.append(f"VAT: {f['vat_total']}")
    lines.append(f"Total due: {f.get('total')} {f.get('currency')}")
    lines += _pay(f.get("payment_details"))
    if f.get("notes"):
        lines.append(str(f["notes"]))
    return "\n".join(lines)


def table(f: dict) -> str:
    rows = [f"{f['supplier_name']}", f"{f.get('supplier_address') or ''}", f"Registered for VAT: {f['vat_number']}" if f.get("vat_number") else "", "", f"Bill to | {f.get('customer_name') or '-'} | {f.get('customer_address') or '-'}", f"Reference | {f['invoice_number']}", f"Issued | {f.get('issue_date')}", f"Payment due | {f.get('due_date') or '-'}", "", "Item | Qty | Unit | Net | Rate | VAT"]
    for line in f.get("line_items") or []:
        rows.append(f"{line.get('description')} | {line.get('quantity', '-')} | {line.get('unit_price', '-')} | {line.get('net')} | {line.get('vat_rate', '-')} | {line.get('vat', '-')}")
    rows += ["", f"Net total | {f.get('subtotal')}", f"VAT total | {f.get('vat_total') or '0.00'}", f"Amount payable ({f.get('currency')}) | {f.get('total')}", ""]
    rows += _pay(f.get("payment_details"))
    if f.get("notes"):
        rows.append(str(f["notes"]))
    return "\n".join(rows)


def email(f: dict) -> str:
    parts = [f"Subject: Invoice {f['invoice_number']} from {f['supplier_name']}", "", "Hello,", "", f"Please find our invoice {f['invoice_number']}, dated {f.get('issue_date')}, for {f.get('customer_name') or 'your business'}" + (f" at {f['customer_address']}" if f.get("customer_address") else "") + "."]
    for line in f.get("line_items") or []:
        parts.append(f"- {line.get('description')}: {line.get('quantity', '-')} at {line.get('unit_price', '-')} = {line.get('net')} net, VAT {line.get('vat_rate', '-')}% {line.get('vat', '-')}")
    parts.append(f"That is {f.get('subtotal')} before VAT, {f.get('vat_total') or 'no'} VAT, and {f.get('total')} {f.get('currency')} in total.")
    if f.get("due_date"):
        parts.append(f"Please pay by {f['due_date']}.")
    parts += _pay(f.get("payment_details"))
    if f.get("notes"):
        parts.append(str(f["notes"]))
    parts += ["", "Kind regards,", f"{f['supplier_name']}, {f.get('supplier_address') or ''}"]
    if f.get("vat_number"):
        parts.append(f"VAT reg {f['vat_number']}")
    return "\n".join(parts)


def compact(f: dict) -> str:
    bits = [f"DOC#{f['invoice_number']}", f"SELLER={f['supplier_name']}; ADDR={f.get('supplier_address') or ''}"]
    if f.get("vat_number"):
        bits.append(f"VATNO={f['vat_number']}")
    if f.get("customer_name"):
        bits.append(f"BUYER={f['customer_name']}; {f.get('customer_address') or ''}")
    bits.append(f"ISSUED={f.get('issue_date')}")
    if f.get("due_date"):
        bits.append(f"PAYBY={f['due_date']}")
    for line in f.get("line_items") or []:
        bits.append(f"LINE={line.get('description')}; QTY={line.get('quantity', '-')}; UNIT={line.get('unit_price', '-')}; NET={line.get('net')}; RATE={line.get('vat_rate', '-')}; TAX={line.get('vat', '-')}")
    bits.append(f"NET={f.get('subtotal')}; TAX={f.get('vat_total') or '0.00'}; GROSS={f.get('total')}; CCY={f.get('currency')}")
    bits += _pay(f.get("payment_details"))
    if f.get("notes"):
        bits.append(f"MEMO={f['notes']}")
    return "\n".join(bits)


def letter(f: dict) -> str:
    text = [f"{f['supplier_name']}", f"{f.get('supplier_address') or ''}", "", f"Dear {f.get('customer_name') or 'Customer'},", ""]
    if f.get("customer_address"):
        text.insert(3, f"Addressed to {f['customer_address']}")
    for line in f.get("line_items") or []:
        text.append(f"We charge you {line.get('net')} excluding VAT for {line.get('description')} (quantity {line.get('quantity', '-')} at {line.get('unit_price', '-')} each). VAT at {line.get('vat_rate', '-')} percent is {line.get('vat', '-')}.")
    text.append(f"The sum before VAT is {f.get('subtotal')}, VAT comes to {f.get('vat_total') or 'nil'}, so the balance owing is {f.get('total')} {f.get('currency')}.")
    text.append(f"This statement bears the number {f['invoice_number']} and was issued on {f.get('issue_date')}.")
    if f.get("due_date"):
        text.append(f"We ask for settlement no later than {f['due_date']}.")
    text += _pay(f.get("payment_details"))
    if f.get("notes"):
        text.append(str(f["notes"]))
    if f.get("vat_number"):
        text.append(f"Our VAT registration is {f['vat_number']}.")
    return "\n".join(text)


LAYOUTS = {"labelled": labelled, "table": table, "email": email, "compact": compact, "letter": letter}


def render(layout: str, fields: dict) -> str:
    return LAYOUTS[layout](fields)


def quotes_for(layout_text: str, fields: dict) -> list[dict]:
    """The exact quotes a faithful extraction would return, for the suites that replace the model."""
    out = []
    values = {**fields, **(fields.get("payment_details") or {})}
    for name in ("supplier_name", "invoice_number", "total", "due_date", "wallet_address", "iban", "account_number", "token_contract"):
        value = values.get(name)
        if value:
            line = next((ln for ln in layout_text.splitlines() if str(value) in ln), str(value))
            out.append({"field": name, "quote": line})
    return out

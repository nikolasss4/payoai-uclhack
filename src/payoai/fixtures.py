"""Synthetic business data for the demo, tests and benchmark. All of it is invented.

Addresses and account numbers are made up and belong to nobody.
"""

from datetime import date, timedelta

from payoai.db import Jsonb, new_id, tx
from payoai.providers.simulator import set_balance

BASE_SEPOLIA = 84532
SYNTHETIC_USDC = "0x" + "a1" * 20
ALEX_WALLET = "0x7f3a9c2e41b8d6f05e1c7a93d2b4f8e6c0a1d3b5"
POISONED_WALLET = "0x7f3a11111111111111111111111111111111d3b5"
FIGMA_IBAN = "GB33BUKB20201555555555"
FRAUD_IBAN = "GB94BARC10201530093459"


def seed_business(team_id: str, approver_id: str, balances: dict | None = None) -> dict:
    """A fictional UK company with verified suppliers, a contractor paid in USDC, and two accounts."""
    balances = balances or {}
    today = date.today().isoformat()
    ids = {k: new_id("cp") for k in ("figma", "alex", "cleaners")}
    gbp_external, usdc_external = f"ext_{team_id}_gbp", f"ext_{team_id}_usdc"
    ids.update(gbp=new_id("acct"), usdc=new_id("acct"), figma_dest=new_id("dest"), alex_dest=new_id("dest"), cleaners_dest=new_id("dest"))
    with tx() as conn:
        for key, name, legal, kind, labels, low, high in (
            ("figma", "Figma", "Figma Design UK Ltd", "business", ["supplier"], 30000, 60000),
            ("alex", "Alex Chen", "Alex Chen", "individual", ["contractor"], 250000000, 350000000),
            ("cleaners", "Brightside Cleaning", "Brightside Cleaning Ltd", "business", ["supplier"], 10000, 40000),
        ):
            conn.execute(
                "INSERT INTO counterparties (id, team_id, display_name, legal_name, entity_type, classifications, usual_amount_min_minor, usual_amount_max_minor, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ids[key], team_id, name, legal, kind, Jsonb(labels), low, high, approver_id),
            )
            conn.execute(
                "INSERT INTO contact_channels (id, team_id, counterparty_id, kind, value) VALUES (%s, %s, %s, 'phone', %s)",
                (new_id("chn"), team_id, ids[key], "020 7946 0100"),
            )
        for key, cp, rail, asset, masked, details, chain, token in (
            ("figma_dest", "figma", "FIAT_BANK", "GBP", "GB33…5555", {"iban": FIGMA_IBAN}, None, None),
            ("alex_dest", "alex", "CRYPTO", "USDC", "0x7f3a…d3b5", {"address": ALEX_WALLET}, BASE_SEPOLIA, SYNTHETIC_USDC),
            ("cleaners_dest", "cleaners", "FIAT_BANK", "GBP", "60-16-13 …6819", {"sort_code": "601613", "account_number": "31926819"}, None, None),
        ):
            conn.execute(
                "INSERT INTO payment_destinations (id, team_id, counterparty_id, rail, asset, masked_display, details, chain_id, token_contract, status, source, added_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'VERIFIED', 'seed', %s)",
                (ids[key], team_id, ids[cp], rail, asset, masked, Jsonb(details), chain, token, approver_id),
            )
            conn.execute(
                "INSERT INTO destination_verifications (id, team_id, destination_id, method, evidence, verified_by) VALUES (%s, %s, %s, 'CALL_BACK', 'Synthetic verification for the demo', %s)",
                (new_id("ver"), team_id, ids[key], approver_id),
            )
        conn.execute(
            "INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, external_id, reserve_minor) VALUES (%s, %s, 'Main GBP account', 'SIMULATOR', 'FIAT_BANK', 'GBP', %s, %s)",
            (ids["gbp"], team_id, gbp_external, balances.get("gbp_reserve", 300000)),
        )
        conn.execute(
            "INSERT INTO financial_accounts (id, team_id, name, provider, rail, asset, chain_id, token_contract, external_id, reserve_minor) VALUES (%s, %s, 'USDC wallet', 'SIMULATOR', 'CRYPTO', 'USDC', %s, %s, %s, 0)",
            (ids["usdc"], team_id, BASE_SEPOLIA, SYNTHETIC_USDC, usdc_external),
        )
    set_balance(gbp_external, balances.get("gbp", 1000000))
    set_balance(usdc_external, balances.get("usdc", 2000000000))
    ids.update(gbp_external=gbp_external, usdc_external=usdc_external, seeded_on=today)
    return ids


def invoice_fields(invoice_number: str = "INV-1042", total: str = "480.00", subtotal: str = "400.00", vat: str = "80.00", payment: dict | None = None, supplier: str = "Figma Design UK Ltd", **overrides) -> dict:
    """What a correct extraction of a synthetic invoice looks like."""
    fields = {
        "supplier_name": supplier,
        "supplier_address": "1 Example Street, London EC1A 1AA",
        "supplier_country": "GB",
        "vat_number": "GB123456789",
        "invoice_number": invoice_number,
        "issue_date": date.today().isoformat(),
        "supply_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=14)).isoformat(),
        "customer_name": "Northwind Studio Ltd",
        "customer_address": "2 Sample Road, London N1 1AA",
        "currency": "GBP",
        "subtotal": subtotal,
        "vat_total": vat,
        "total": total,
        "line_items": [{"description": "Design software seats", "quantity": "4", "unit_price": "100.00", "net": subtotal, "vat_rate": "20", "vat": vat}],
        "payment_details": payment if payment is not None else {"iban": FIGMA_IBAN},
        "quotes": [],
        "suspicious_text": [],
        "notes": None,
    }
    fields.update(overrides)
    return fields


def render_invoice(fields: dict) -> str:
    """The invoice as plain text, with the quotes a correct extraction would cite."""
    lines = [
        f"INVOICE {fields['invoice_number']}",
        f"From: {fields['supplier_name']}, {fields.get('supplier_address') or ''}",
        f"VAT number: {fields.get('vat_number') or ''}",
        f"To: {fields.get('customer_name') or ''}, {fields.get('customer_address') or ''}",
        f"Date: {fields.get('issue_date') or ''}",
        f"Due date: {fields.get('due_date') or ''}",
    ]
    for line in fields.get("line_items") or []:
        lines.append(f"{line.get('description')}: quantity {line.get('quantity')}, unit price {line.get('unit_price')}, net {line.get('net')}, VAT at {line.get('vat_rate')}%: {line.get('vat')}")
    lines += [f"Subtotal: {fields.get('subtotal')}", f"VAT: {fields.get('vat_total')}", f"Total due: {fields.get('total')} {fields.get('currency')}"]
    for key, value in (fields.get("payment_details") or {}).items():
        if value:
            lines.append(f"Pay to {key}: {value}")
    if fields.get("notes"):
        lines.append(str(fields["notes"]))
    return "\n".join(lines)


def with_quotes(fields: dict) -> dict:
    """Adds the exact quotes a faithful extraction would return."""
    quotes = [
        {"field": "supplier_name", "quote": f"From: {fields['supplier_name']}"},
        {"field": "invoice_number", "quote": f"INVOICE {fields['invoice_number']}"},
        {"field": "total", "quote": f"Total due: {fields.get('total')}"},
        {"field": "due_date", "quote": f"Due date: {fields.get('due_date')}"},
    ]
    for key, value in (fields.get("payment_details") or {}).items():
        if value:
            quotes.append({"field": key, "quote": f"Pay to {key}: {value}"})
    return {**fields, "quotes": quotes}

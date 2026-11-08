"""Exact money and payment-hash code. No I/O and no floats."""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

DECIMALS = {"GBP": 2, "USD": 2, "EUR": 2, "USDC": 6}
# Commas only as thousands separators, so "12,00" can't silently become 1200.
PLAIN_AMOUNT = re.compile(r"^[+-]?((\d+|\d{1,3}(,\d{3})+)(\.\d*)?|\.\d+)$")


def to_minor(amount: str | int | Decimal, asset: str) -> int:
    """Converts a decimal amount such as "1250.00" to integer minor units, refusing lost precision."""
    if isinstance(amount, float):
        raise TypeError("money must not be a float")
    if asset not in DECIMALS:
        raise ValueError(f"unsupported asset {asset}")
    text = format(amount, "f") if isinstance(amount, Decimal) and amount.is_finite() else str(amount).strip()
    if not PLAIN_AMOUNT.match(text):
        raise ValueError(f"invalid amount {amount!r}")
    try:
        value = Decimal(text.replace(",", "")) * (Decimal(10) ** DECIMALS[asset])
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount {amount!r}") from exc
    if value != value.to_integral_value():
        raise ValueError(f"{amount} has too many decimal places for {asset}")
    return int(value)


def format_minor(amount: int | Decimal, asset: str) -> str:
    """Shows two decimals. Extra places (USDC has six) appear only when they aren't zero, so no amount is ever rounded away."""
    places = DECIMALS.get(asset, 2)
    value = Decimal(int(amount)) / (Decimal(10) ** places)
    text = f"{value:,.{places}f}"
    if places > 2:
        whole, frac = text.split(".")
        text = f"{whole}.{frac.rstrip('0').ljust(2, '0')}"
    return f"£{text}" if asset == "GBP" else f"{text} {asset}"


def canonical_payload(
    *,
    team_id: str,
    source_account_id: str,
    destination_id: str,
    destination_details: dict,
    rail: str,
    provider: str,
    amount_minor: int,
    asset: str,
    max_fee_minor: int,
    obligation_id: str,
    payment_reference: str,
    chain_id: int | None = None,
    token_contract: str | None = None,
) -> dict:
    """Every field an approval binds to. Changing any of them changes the hash."""
    return {
        "team_id": team_id,
        "source_account_id": source_account_id,
        "destination_id": destination_id,
        "destination_details": destination_details,
        "rail": rail,
        "provider": provider,
        "amount_minor": str(int(amount_minor)),
        "asset": asset,
        "max_fee_minor": str(int(max_fee_minor)),
        "obligation_id": obligation_id,
        "payment_reference": payment_reference,
        "chain_id": chain_id,
        "token_contract": token_contract,
    }


def payload_hash(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

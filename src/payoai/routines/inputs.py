"""What the Main Loop may pass into each Routine. Extra fields are refused.

Bank and wallet details aren't accepted anywhere. Amounts are converted by code, and team and role
come from the session. A Routine finds the Verified Destination itself.
"""

import re

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from payoai.domain.money import DECIMALS, to_minor

BANK_OR_WALLET = re.compile(r"(\b[A-Z]{2}\d{2}\s?[A-Z0-9 ]{11,34}\b|0x[a-fA-F0-9]{20,}|\b\d{2}-\d{2}-\d{2}\b|\b\d{8}\b)")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def no_details(cls, value):
        if isinstance(value, str) and BANK_OR_WALLET.search(value):
            raise ValueError("Bank or wallet details can't be passed here. Add them with Add Counterparty, then verify them.")
        return value


class BillInput(Strict):
    document_id: str | None = None
    obligation_id: str | None = None


class PaySomeoneInput(Strict):
    counterparty_name: str
    amount: str
    asset: str
    purpose: str | None = None


class ReplaceBillInput(Strict):
    obligation_id: str
    document_id: str | None = None
    corrections: dict[str, str] | None = None
    reason: str | None = None


class BatchInput(Strict):
    due_from: str | None = None
    due_to: str | None = None


class CashInput(Strict):
    days: int = 7


class AddCounterpartyInput(BaseModel):
    """The one place new details are accepted. They are saved as UNVERIFIED and can't be paid."""

    model_config = ConfigDict(extra="forbid")
    display_name: str
    legal_name: str | None = None
    entity_type: str = "business"
    classifications: list[str] = []
    contact_phone: str | None = None
    rail: str | None = None
    asset: str | None = None
    iban: str | None = None
    sort_code: str | None = None
    account_number: str | None = None
    wallet_address: str | None = None
    chain_id: int | None = None
    token_contract: str | None = None
    source: str = "chat"


class RecurringInput(Strict):
    counterparty_name: str
    max_amount: str
    asset: str
    frequency: str
    day_of_period: int
    valid_until: str
    purpose: str | None = None
    required_evidence: list[str] = []


class ReviseExpenseInput(Strict):
    expense_id: str
    category: str | None = None
    business_purpose: str | None = None
    reason: str


class ChaseInput(Strict):
    expense_id: str


class ResearchInput(Strict):
    use_case: str
    budget: str | None = None
    asset: str = "GBP"
    min_specs: str | None = None
    preferred_merchants: list[str] = []
    delivery_by: str | None = None


class CheckoutInput(Strict):
    merchant: str
    sku: str
    quantity: int = 1
    budget: str | None = None


class SavePreferenceInput(Strict):
    type: str
    subject: str | None = None
    value: dict
    scope: str = "team"


SCHEMAS = {
    "pay_bill": BillInput,
    "register_bill": BillInput,
    "check_bill_compliance": BillInput,
    "pay_someone": PaySomeoneInput,
    "replace_bill": ReplaceBillInput,
    "prepare_batch": BatchInput,
    "cash_analysis": CashInput,
    "add_counterparty": AddCounterpartyInput,
    "set_up_recurring": RecurringInput,
    "revise_expense": ReviseExpenseInput,
    "chase_missing_document": ChaseInput,
    "purchase_research": ResearchInput,
    "purchase_checkout": CheckoutInput,
    "save_preference": SavePreferenceInput,
}


def validate(routine: str, inputs: dict) -> dict:
    schema = SCHEMAS.get(routine)
    if schema is None:
        raise ValueError(f"unknown routine {routine}")
    try:
        clean = schema(**inputs).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError("; ".join(e["msg"] for e in exc.errors())) from exc
    if routine in ("pay_bill", "register_bill", "check_bill_compliance") and not (clean.get("document_id") or clean.get("obligation_id")):
        raise ValueError("Give a document_id or an obligation_id.")
    if routine == "pay_someone":
        asset = clean["asset"].upper()
        if asset not in DECIMALS:
            raise ValueError(f"unsupported asset {asset}")
        clean["asset"] = asset
        clean["amount_minor"] = str(to_minor(clean.pop("amount"), asset))
    return clean


def describe(routine: str) -> dict:
    schema = SCHEMAS[routine]
    return {name: ("required" if f.is_required() else "optional") for name, f in schema.model_fields.items()}

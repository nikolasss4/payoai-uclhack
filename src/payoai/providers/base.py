"""The interface every payment rail implements. Capabilities differ, so adapters declare them."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class SubmitRequest:
    idempotency_key: str
    account_external_id: str
    amount_minor: int
    asset: str
    destination_details: dict
    reference: str
    max_fee_minor: int = 0
    chain_id: int | None = None
    token_contract: str | None = None


@dataclass
class SubmitResult:
    # NOT_SENT: provably never left. UNKNOWN: may have been processed.
    outcome: str  # ACCEPTED | REJECTED | NOT_SENT | UNKNOWN
    external_id: str | None = None
    reason: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class StatusResult:
    status: str  # COMPLETED | FAILED | PENDING | NOT_FOUND | UNREACHABLE
    external_id: str | None = None
    amount_minor: int | None = None
    asset: str | None = None
    destination_ref: str | None = None
    reference: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Balance:
    confirmed_minor: int
    available_minor: int
    as_of: datetime
    source: str


class ProviderUnavailable(Exception):
    pass


class PaymentProvider(Protocol):
    name: str
    evidence_label: str
    capabilities: dict[str, bool]

    def get_balance(self, account_external_id: str) -> Balance: ...

    def submit(self, request: SubmitRequest) -> SubmitResult: ...

    def get_status(self, external_id: str | None, reference: str, idempotency_key: str) -> StatusResult: ...

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]: ...

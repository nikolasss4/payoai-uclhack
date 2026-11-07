"""Simulated payment provider with deterministic fault injection.

Its ledger (`sim_transfers`) stands in for the provider's own records, so a judge can count how
many transfers actually happened, independently of PayoAI's state. Faults are keyed by payment
reference and are set by tests and eval cases.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from payoai.db import Jsonb, fetch_one, new_id, tx
from payoai.providers.base import Balance, ProviderUnavailable, StatusResult, SubmitRequest, SubmitResult

WEBHOOK_SECRET_ENV = "WEBHOOK_SECRET_SIMULATOR"


def destination_ref(details: dict) -> str:
    return details.get("address") or details.get("iban") or details.get("account_number") or json.dumps(details, sort_keys=True)


def set_fault(reference: str, **fault: Any) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO sim_faults (reference, fault) VALUES (%s, %s) ON CONFLICT (reference) DO UPDATE SET fault = EXCLUDED.fault",
            (reference, Jsonb(fault)),
        )


def set_balance(account_external_id: str, available_minor: int, stale_hours: int = 0) -> None:
    stale = datetime.now(timezone.utc) - timedelta(hours=stale_hours) if stale_hours else None
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO sim_balances (account_external_id, available_minor, stale_since) VALUES (%s, %s, %s)
            ON CONFLICT (account_external_id) DO UPDATE SET available_minor = EXCLUDED.available_minor, stale_since = EXCLUDED.stale_since
            """,
            (account_external_id, available_minor, stale),
        )


def ledger(reference: str) -> list[dict]:
    with tx() as conn:
        return conn.execute("SELECT * FROM sim_transfers WHERE reference = %s", (reference,)).fetchall()


class SimulatorProvider:
    evidence_label = "SIMULATED"
    capabilities = {"webhooks": True, "fx_quote": False, "cancellation": False, "idempotency_keys": True}

    def __init__(self, name: str = "SIMULATOR"):
        self.name = name

    def get_balance(self, account_external_id: str) -> Balance:
        with tx() as conn:
            row = fetch_one(conn, "SELECT * FROM sim_balances WHERE account_external_id = %s", (account_external_id,))
        if row is None:
            raise ProviderUnavailable(f"no balance for {account_external_id}")
        as_of = row["stale_since"] or datetime.now(timezone.utc)
        return Balance(int(row["available_minor"]), int(row["available_minor"]), as_of, "simulator")

    def submit(self, request: SubmitRequest) -> SubmitResult:
        fault = self._fault(request.reference)
        kind = fault.get("submit")
        if kind == "connection_failed":
            # Fails once, before anything is sent.
            if fault.get("times", 1) > 0:
                set_fault(request.reference, **{**fault, "times": fault.get("times", 1) - 1})
                return SubmitResult("NOT_SENT", reason="connection failed before the request was sent")
        if kind == "timeout_before_accept":
            # The caller can't tell this from a timeout after acceptance.
            return SubmitResult("UNKNOWN", reason="timeout: no response")
        if kind == "reject":
            return SubmitResult("REJECTED", reason=fault.get("reason", "invalid destination details"))

        status = "PENDING" if fault.get("pending_polls") or fault.get("final") else "COMPLETED"
        amount = int(fault.get("settled_amount_minor", request.amount_minor))
        with tx() as conn:
            row = conn.execute(
                """
                INSERT INTO sim_transfers (external_id, provider, idempotency_key, account_external_id, amount_minor, asset, destination_ref, reference, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider, idempotency_key) DO NOTHING
                RETURNING external_id
                """,
                (
                    new_id("simtx"),
                    self.name,
                    request.idempotency_key,
                    request.account_external_id,
                    amount,
                    request.asset,
                    destination_ref(request.destination_details),
                    request.reference,
                    status,
                ),
            ).fetchone()
            if row is None and fault.get("ignores_idempotency_key"):
                row = conn.execute(
                    """
                    INSERT INTO sim_transfers (external_id, provider, idempotency_key, account_external_id, amount_minor, asset, destination_ref, reference, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING external_id
                    """,
                    (new_id("simtx"), self.name, new_id("dup"), request.account_external_id, amount, request.asset, destination_ref(request.destination_details), request.reference, status),
                ).fetchone()
            existing = row or fetch_one(
                conn,
                "SELECT external_id FROM sim_transfers WHERE provider = %s AND idempotency_key = %s",
                (self.name, request.idempotency_key),
            )
            conn.execute(
                "UPDATE sim_balances SET available_minor = available_minor - %s WHERE account_external_id = %s",
                (amount if row else 0, request.account_external_id),
            )
        if kind == "timeout_after_accept":
            return SubmitResult("UNKNOWN", reason="timeout: no response")
        if kind == "server_error":
            return SubmitResult("UNKNOWN", reason="provider returned 500")
        return SubmitResult("ACCEPTED", external_id=existing["external_id"], raw={"status": status})

    def get_status(self, external_id: str | None, reference: str, idempotency_key: str) -> StatusResult:
        fault = self._fault(reference)
        if fault.get("status_unreachable"):
            return StatusResult("UNREACHABLE")
        with tx() as conn:
            row = fetch_one(
                conn,
                "SELECT * FROM sim_transfers WHERE provider = %s AND idempotency_key = %s",
                (self.name, idempotency_key),
            )
            if row is None:
                return StatusResult("NOT_FOUND")
            polls = conn.execute(
                "UPDATE sim_transfers SET polls = polls + 1 WHERE external_id = %s RETURNING polls", (row["external_id"],)
            ).fetchone()["polls"]
            status = row["status"]
            if status == "PENDING":
                if polls > int(fault.get("pending_polls", 0)):
                    status = fault.get("final", "COMPLETED")
                    conn.execute("UPDATE sim_transfers SET status = %s WHERE external_id = %s", (status, row["external_id"]))
                    if status == "FAILED":
                        conn.execute(
                            "UPDATE sim_balances SET available_minor = available_minor + %s WHERE account_external_id = %s",
                            (row["amount_minor"], row["account_external_id"]),
                        )
        return StatusResult(
            status,
            external_id=row["external_id"],
            amount_minor=int(row["amount_minor"]),
            asset=row["asset"],
            destination_ref=row["destination_ref"],
            reference=row["reference"],
            raw={"polls": polls},
        )

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        secret = os.environ.get(WEBHOOK_SECRET_ENV, "simulator-webhook-secret").encode()
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-sim-signature", "")):
            raise ValueError("invalid webhook signature")
        return json.loads(raw_body)

    def _fault(self, reference: str) -> dict:
        with tx() as conn:
            row = fetch_one(conn, "SELECT fault FROM sim_faults WHERE reference = %s", (reference,))
        return row["fault"] if row else {}


def sign_webhook(body: bytes) -> str:
    secret = os.environ.get(WEBHOOK_SECRET_ENV, "simulator-webhook-secret").encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()

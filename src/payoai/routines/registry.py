"""Routine configuration. Start Confirmation and allowed roles are enforced by code, not the model."""

from dataclasses import dataclass
from typing import Callable

from payoai.routines.graphs import others, payment


@dataclass(frozen=True)
class RoutineConfig:
    name: str
    start_confirmation: bool
    roles: tuple[str, ...]
    build: Callable | None = None
    version: str = "1"
    chat_startable: bool = True


WRITERS = ("OPERATOR", "APPROVER")

ROUTINES = {
    "pay_bill": RoutineConfig("Pay Bill", True, WRITERS, payment.pay_bill),
    "replace_bill": RoutineConfig("Replace Bill", True, WRITERS, others.replace_bill),
    "pay_someone": RoutineConfig("Pay Someone", True, WRITERS, others.pay_someone),
    "prepare_batch": RoutineConfig("Prepare Batch", True, WRITERS, others.prepare_batch),
    "register_bill": RoutineConfig("Register Bill", False, WRITERS, payment.register_bill),
    "check_bill_compliance": RoutineConfig("Check Bill Compliance", False, WRITERS, others.check_bill_compliance),
    "cash_analysis": RoutineConfig("Cash Analysis", False, ("VIEWER", *WRITERS), others.cash_analysis),
    "add_counterparty": RoutineConfig("Add Counterparty", True, WRITERS, others.add_counterparty),
    "set_up_recurring": RoutineConfig("Set Up Recurring", True, WRITERS, others.set_up_recurring),
    "prepare_occurrence": RoutineConfig("Prepare Occurrence", False, WRITERS, others.prepare_occurrence, chat_startable=False),
    "revise_expense": RoutineConfig("Revise Expense", True, WRITERS, others.revise_expense),
    "chase_missing_document": RoutineConfig("Chase Missing Document", False, WRITERS, others.chase_missing_document),
    "purchase_research": RoutineConfig("Purchase Research", False, WRITERS, others.purchase_research),
    "purchase_checkout": RoutineConfig("Purchase Checkout", True, WRITERS, others.purchase_checkout),
    "save_preference": RoutineConfig("Save Preference", True, WRITERS, others.save_preference),
}


def get_routine(name: str) -> RoutineConfig:
    if name not in ROUTINES:
        raise KeyError(f"unknown routine {name}")
    return ROUTINES[name]

"""Routine configuration. Start Confirmation and allowed roles are enforced by code, not the model."""

from dataclasses import dataclass
from typing import Callable

from payoai.routines.graphs import payment


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
    "register_bill": RoutineConfig("Register Bill", False, WRITERS, payment.register_bill),
}


def get_routine(name: str) -> RoutineConfig:
    if name not in ROUTINES:
        raise KeyError(f"unknown routine {name}")
    return ROUTINES[name]

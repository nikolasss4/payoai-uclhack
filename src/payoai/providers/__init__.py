"""Returns the adapter for a provider name. Only money-worker should call submit."""

from payoai.providers.base import PaymentProvider
from payoai.providers.simulator import SimulatorProvider

_providers: dict[str, PaymentProvider] = {}


def get_provider(name: str) -> PaymentProvider:
    if name not in _providers:
        # Every account runs on the simulator until a real adapter is added and tested.
        _providers[name] = SimulatorProvider(name)
    return _providers[name]

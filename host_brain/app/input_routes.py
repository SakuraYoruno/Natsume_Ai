"""HTTP input adapter; business behavior lives in :mod:`host_brain.input_runtime`."""

from host_brain.input_runtime.contracts_and_normalization import GovernedCommandApplicationProvider, InputPayload, InputResponse
from host_brain.input_runtime.ingress import receive_input
from host_brain.input_runtime.worker_dispatch import create_input_router

__all__ = [
    "GovernedCommandApplicationProvider",
    "InputPayload",
    "InputResponse",
    "create_input_router",
    "receive_input",
]

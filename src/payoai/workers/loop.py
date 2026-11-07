"""The shared job loop. A failed job is recorded and never silently retried on the money path."""

import socket
import sys
import time
import traceback
from typing import Callable

from payoai.queue import claim, finish


def run_forever(worker: str, handlers: dict[str, Callable[[dict], None]], idle_seconds: float = 1.0) -> None:
    name = f"{worker}-{socket.gethostname()}"
    print(f"{worker} started, handling {sorted(handlers)}", flush=True)
    while True:
        job = claim(name, list(handlers))
        if job is None:
            time.sleep(idle_seconds)
            continue
        run_one(job, handlers)


def run_one(job: dict, handlers: dict[str, Callable[[dict], None]]) -> None:
    try:
        handlers[job["kind"]](job)
    except Exception as exc:  # A bad job is recorded and doesn't stop the worker.
        traceback.print_exc(file=sys.stderr)
        finish(job["id"], f"{type(exc).__name__}: {exc}"[:1000])
    else:
        finish(job["id"])

"""The shared job loop. A failed job is recorded and never silently retried on the money path."""

import socket
import sys
import threading
import time
import traceback
from typing import Callable

from payoai.queue import claim, finish


def run_forever(worker: str, handlers: dict[str, Callable[[dict], None]], idle_seconds: float = 1.0, concurrency: int = 1) -> None:
    """Runs `concurrency` claim loops. Each holds at most one job, so a long job doesn't block a chat turn."""
    name = f"{worker}-{socket.gethostname()}"
    print(f"{worker} started with {concurrency} slot(s), handling {sorted(handlers)}", flush=True)
    threads = [threading.Thread(target=_slot, args=(f"{name}-{i}", handlers, idle_seconds), daemon=True) for i in range(1, concurrency)]
    for thread in threads:
        thread.start()
    _slot(f"{name}-0" if concurrency > 1 else name, handlers, idle_seconds)


def _slot(name: str, handlers: dict[str, Callable[[dict], None]], idle_seconds: float) -> None:
    while True:
        try:
            job = claim(name, list(handlers))
        except Exception:  # A lost database connection is reported, and the slot keeps polling.
            traceback.print_exc(file=sys.stderr)
            time.sleep(idle_seconds * 5)
            continue
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

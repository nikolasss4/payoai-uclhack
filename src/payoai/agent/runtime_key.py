"""A Gemini key pasted in the app, kept only in the agent worker's memory, for one browser.

Each browser makes a random secret and sends it with every request. The API passes the pasted key to
the worker with Postgres NOTIFY, which isn't written to any table, filed under a hash of that secret.
Jobs the browser starts carry only the hash, and the worker uses the matching key while it runs them.
Another browser has a different secret, so it can't use the key. Nothing stores it: it's gone when the
worker restarts, when the browser signs out, or after KEY_HOURS.
"""

import contextvars
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager

import psycopg

from payoai.settings import schema

KEY_HOURS = 12
_keys: dict[str, tuple[str, float]] = {}
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("gemini_key_ref", default=None)
_SECRET = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def ref_for(browser_secret: str | None) -> str | None:
    """The hash a browser's key is filed under. A missing or malformed secret has none."""
    if not browser_secret or not _SECRET.match(browser_secret):
        return None
    return hashlib.sha256(browser_secret.encode()).hexdigest()


def channel() -> str:
    # Channels are shared by the whole database, so each schema (and test run) gets its own.
    return f"gemini_key_{schema()}"


@contextmanager
def using(ref: str | None):
    """Runs a job with the key of the browser that started it, if it pasted one."""
    token = _current.set(ref)
    try:
        yield
    finally:
        _current.reset(token)


def current_ref() -> str | None:
    return _current.get()


def get() -> str | None:
    ref = _current.get()
    entry = _keys.get(ref) if ref else None
    if entry is None:
        return None
    key, expires = entry
    if time.time() > expires:
        _keys.pop(ref, None)
        return None
    return key


def put(ref: str, key: str | None) -> None:
    if key:
        _keys[ref] = (key, time.time() + KEY_HOURS * 3600)
    else:
        _keys.pop(ref, None)


def send(conn, ref: str, key: str | None) -> None:
    """Delivered to the listening worker when `conn`'s transaction commits. A None key forgets it."""
    conn.execute("SELECT pg_notify(%s, %s)", (channel(), json.dumps({"ref": ref, "api_key": key})))


def _listen_forever() -> None:
    while True:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
                conn.execute(f'LISTEN "{channel()}"')
                for note in conn.notifies():
                    try:
                        data = json.loads(note.payload)
                        put(str(data["ref"]), data.get("api_key") or None)
                    except (ValueError, KeyError, TypeError):
                        continue
        except Exception:  # A lost connection is reported, then the listener reconnects.
            traceback.print_exc(file=sys.stderr)
            time.sleep(5)


def start_listener() -> None:
    threading.Thread(target=_listen_forever, name="gemini-key-listener", daemon=True).start()

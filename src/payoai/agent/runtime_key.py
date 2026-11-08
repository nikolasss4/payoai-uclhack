"""A Gemini key pasted in the app, kept only in the agent worker's memory.

The API passes it on with Postgres NOTIFY, which isn't written to any table. The worker listens and
keeps it per team until it restarts. Nothing stores it, so after a restart the chat asks for it again.
"""

import json
import os
import sys
import threading
import time
import traceback

import psycopg

from payoai.settings import schema

_keys: dict[str, str] = {}


def channel() -> str:
    # Channels are shared by the whole database, so each schema (and test run) gets its own.
    return f"gemini_key_{schema()}"


def get(team_id: str | None) -> str | None:
    return _keys.get(team_id) if team_id else None


def put(team_id: str, key: str) -> None:
    _keys[team_id] = key


def send(conn, team_id: str, key: str) -> None:
    """Delivered to the listening worker when `conn`'s transaction commits."""
    conn.execute("SELECT pg_notify(%s, %s)", (channel(), json.dumps({"team_id": team_id, "api_key": key})))


def _listen_forever() -> None:
    while True:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
                conn.execute(f'LISTEN "{channel()}"')
                for note in conn.notifies():
                    try:
                        data = json.loads(note.payload)
                        put(str(data["team_id"]), str(data["api_key"]))
                    except (ValueError, KeyError, TypeError):
                        continue
        except Exception:  # A lost connection is reported, then the listener reconnects.
            traceback.print_exc(file=sys.stderr)
            time.sleep(5)


def start_listener() -> None:
    threading.Thread(target=_listen_forever, name="gemini-key-listener", daemon=True).start()

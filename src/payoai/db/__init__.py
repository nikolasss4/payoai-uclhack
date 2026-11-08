"""Database access. Every connection uses the private schema, never Supabase's public one.

A process can set DATABASE_TX_URL to use Supabase's transaction pooler (port 6543) for its main pool,
so it doesn't count against the session pooler's client limit. There a server connection is shared
between transactions, so the schema is set inside each transaction instead of once per connection.
Anything that needs a whole session (LISTEN, the LangGraph checkpointer, migrations) keeps DATABASE_URL.
"""

import os
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from payoai.settings import schema

_pool: ConnectionPool | None = None


def transaction_pooled() -> bool:
    return bool(os.environ.get("DATABASE_TX_URL"))


def _configure(conn: psycopg.Connection) -> None:
    if transaction_pooled():
        return  # A session setting wouldn't last past this transaction. tx() sets it each time.
    conn.execute(f'SET search_path TO "{schema()}"')
    conn.commit()


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ.get("DATABASE_TX_URL") or os.environ["DATABASE_URL"],
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_SIZE", "4")),
            configure=_configure,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    """One transaction: commits on success and rolls back on any error."""
    with pool().connection() as conn:
        if transaction_pooled():
            conn.execute(f'SET LOCAL search_path TO "{schema()}"')
        yield conn


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def fetch_one(conn: psycopg.Connection, sql: str, params=None) -> dict | None:
    return conn.execute(sql, params).fetchone()


def fetch_all(conn: psycopg.Connection, sql: str, params=None) -> list[dict]:
    return conn.execute(sql, params).fetchall()


__all__ = ["Jsonb", "close_pool", "fetch_all", "fetch_one", "new_id", "pool", "tx"]

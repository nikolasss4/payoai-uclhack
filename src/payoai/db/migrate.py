"""Applies numbered SQL migrations from migrations/ in order, once each."""

import os

import psycopg

from payoai.settings import ROOT, load_env, schema


def migrate(drop_first: bool = False) -> list[str]:
    target = schema()
    applied = []
    with psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None) as conn:
        if drop_first:
            if target == "payoai":
                raise RuntimeError("refusing to drop the main schema")
            conn.execute(f'DROP SCHEMA IF EXISTS "{target}" CASCADE')
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{target}"')
        conn.execute(f'SET search_path TO "{target}"')
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    load_env(".env.shared")
    print("applied:", migrate() or "nothing new")

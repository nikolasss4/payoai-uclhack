"""Tests run against a separate schema, dropped and recreated each session, never the main one."""

import os

import pytest

os.environ["PAYOAI_SCHEMA"] = "payoai_test"

from payoai.settings import load_env  # noqa: E402

load_env(".env.shared")

from payoai.db import close_pool, new_id, tx  # noqa: E402
from payoai.db.migrate import migrate  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    migrate(drop_first=True)
    yield
    close_pool()


@pytest.fixture()
def team():
    """A new team with one user per role, so tests never share state."""
    team_id = new_id("team")
    users = {role: new_id("user") for role in ("VIEWER", "OPERATOR", "APPROVER")}
    with tx() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (team_id, "Test Ltd"))
        conn.execute("INSERT INTO team_controls (team_id) VALUES (%s)", (team_id,))
        for role, user_id in users.items():
            conn.execute("INSERT INTO users (id, name, email) VALUES (%s, %s, %s)", (user_id, role.title(), f"{user_id}@example.test"))
            conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, %s)", (team_id, user_id, role))
    return {"id": team_id, "users": users}

"""Creates the synthetic demo company in the main schema. Safe to run again."""

from payoai.settings import load_env

load_env(".env.shared")

from payoai.db import fetch_one, tx  # noqa: E402
from payoai.db.migrate import migrate  # noqa: E402
from payoai.fixtures import seed_business  # noqa: E402

TEAM = "team_demo"
USERS = [
    ("user_sam", "Sam Okafor", "sam@northwind.example", "APPROVER"),
    ("user_priya", "Priya Shah", "priya@northwind.example", "APPROVER"),
    ("user_jo", "Jo Marsh", "jo@northwind.example", "OPERATOR"),
    ("user_lee", "Lee Park", "lee@northwind.example", "VIEWER"),
]


def main() -> None:
    print("migrations:", migrate() or "nothing new")
    with tx() as conn:
        if fetch_one(conn, "SELECT id FROM teams WHERE id = %s", (TEAM,)):
            print("demo team already exists")
            return
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, 'Northwind Studio Ltd (synthetic)')", (TEAM,))
        conn.execute("INSERT INTO team_controls (team_id) VALUES (%s)", (TEAM,))
        for user_id, name, email, role in USERS:
            conn.execute("INSERT INTO users (id, name, email) VALUES (%s, %s, %s)", (user_id, name, email))
            conn.execute("INSERT INTO memberships (team_id, user_id, role) VALUES (%s, %s, %s)", (TEAM, user_id, role))
        conn.execute("INSERT INTO users (id, name, email) VALUES ('user_dev', 'Dev', 'dev@payoai.example')")
        conn.execute("INSERT INTO platform_roles (user_id, role) VALUES ('user_dev', 'DEVELOPER')")
    seed_business(TEAM, "user_sam")
    print("demo team created")


if __name__ == "__main__":
    main()

"""One lock order for the money path: the obligation first, then its payment and approval.

Approval, Replace Bill, Record Attempt, Prepare Payment and reconciliation all change the same few
rows. Taking the obligation's lock first makes them queue, so they can't deadlock or interleave.
"""

import psycopg


def lock_obligation(conn: psycopg.Connection, obligation_id: str) -> None:
    conn.execute("SELECT id FROM obligations WHERE id = %s FOR UPDATE", (obligation_id,))

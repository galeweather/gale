"""Gale Coordinator — Credit tracking for workers."""

import sqlite3


def award_credits(
    conn: sqlite3.Connection,
    worker_id: int,
    amount: int,
    reason: str,
    packet_id: int | None = None,
):
    """Award credits to a worker and log the transaction."""
    conn.execute(
        "UPDATE workers SET credits = credits + ? WHERE id = ?",
        (amount, worker_id),
    )
    conn.execute(
        "INSERT INTO credit_transactions (worker_id, amount, reason, packet_id) "
        "VALUES (?, ?, ?, ?)",
        (worker_id, amount, reason, packet_id),
    )

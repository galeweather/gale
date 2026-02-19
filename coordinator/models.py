"""Gale Coordinator — SQLite schema and connection management."""

import sqlite3
import os
from config import settings


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode and row factory."""
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            registered_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_heartbeat TEXT NOT NULL DEFAULT (datetime('now')),
            packets_completed INTEGER NOT NULL DEFAULT 0,
            packets_failed INTEGER NOT NULL DEFAULT 0,
            is_flagged INTEGER NOT NULL DEFAULT 0,
            credits INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL DEFAULT 'render_tile',
            layer TEXT NOT NULL,
            hrrr_run TEXT NOT NULL,
            hrrr_date TEXT NOT NULL,
            hrrr_hour TEXT NOT NULL,
            forecast_hour TEXT NOT NULL DEFAULT 'f00',
            status TEXT NOT NULL DEFAULT 'pending',
            source_url TEXT,
            params TEXT,
            output_prefix TEXT NOT NULL,
            verified_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            UNIQUE(layer, hrrr_run, forecast_hour)
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id INTEGER NOT NULL REFERENCES packets(id),
            worker_id INTEGER NOT NULL REFERENCES workers(id),
            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'assigned',
            result_hash TEXT,
            UNIQUE(packet_id, worker_id)
        );

        CREATE TABLE IF NOT EXISTS credit_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL REFERENCES workers(id),
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            packet_id INTEGER REFERENCES packets(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS hrrr_runs (
            hrrr_run TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'coordinator',
            detected_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_packets_status ON packets(status);
        CREATE INDEX IF NOT EXISTS idx_assignments_packet ON assignments(packet_id);
        CREATE INDEX IF NOT EXISTS idx_assignments_worker ON assignments(worker_id);
        CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status);
    """)
    conn.commit()
    conn.close()

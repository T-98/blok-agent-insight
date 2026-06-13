"""SQLite setup and schema.

Trade-off: sqlite (not Postgres) because this is a single-process sync service
with a tiny dataset and no concurrency story to defend. The schema is the
brief's verbatim. DB_PATH is read from the environment at connect time so tests
can point at a throwaway file without reloading the module.
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "insight.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    first_ts TEXT,
    last_ts TEXT,
    integrity_flag INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    step INTEGER,
    ts TEXT,
    action TEXT,
    target TEXT,
    observation TEXT,
    status TEXT,
    content_hash TEXT,
    injection_flag INTEGER DEFAULT 0,
    UNIQUE(session_id, step, content_hash)
);

CREATE TABLE IF NOT EXISTS rejects (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    raw_json TEXT,
    reason TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS insight_runs (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    prompt_version TEXT,
    model TEXT,
    raw_output TEXT,
    validated_output TEXT,
    validation_status TEXT,
    latency_ms INTEGER,
    created_at TEXT
);
"""


def db_path() -> str:
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def get_conn() -> sqlite3.Connection:
    """One connection per logical operation. check_same_thread=False so the
    FastAPI threadpool can reuse it; the workload is tiny and serialized."""
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        with conn:
            conn.executescript(SCHEMA)
    finally:
        conn.close()

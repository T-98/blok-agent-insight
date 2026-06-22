"""Normalization, dedupe, conflict, injection-scan, and reject logic.

Every reason here is a deterministic computation over the raw event — never an
LLM call. The reject reasons are a closed enum (the brief's contract).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

from app.db import get_conn

# Closed enum — the only reasons we will ever write to the rejects table.
REJECT_DUPLICATE = "duplicate_event"
REJECT_CONFLICT = "conflicting_step"
REJECT_BAD_TIMESTAMP = "bad_timestamp"
REJECT_MISSING_TARGET = "missing_required_target"

# Actions that must carry a target. scroll/observe may have a null target.
TARGET_REQUIRED_ACTIONS = {"click", "navigate"}

# Case-insensitive prompt-injection patterns scanned over the observation text.
INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?previous instructions", re.IGNORECASE),
    re.compile(r"report success", re.IGNORECASE),
    re.compile(r"disregard.*instructions", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
]


def content_hash(action, target, observation, status) -> str:
    """sha256 of canonical JSON of the semantic fields. sort_keys gives a
    stable byte sequence so identical events hash identically across calls."""
    canonical = json.dumps(
        {"action": action, "target": target, "observation": observation, "status": status},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scan_injection(observation) -> bool:
    """True if the observation text matches any known prompt-injection pattern
    (e.g. "ignore previous instructions", "report success"). Deterministic
    regex check — this is what sets injection_flag at ingest, before any LLM
    sees the data. Empty/None observation → False.
    """
    if not observation:
        return False
    return any(p.search(observation) for p in INJECTION_PATTERNS)


def _valid_ts(ts) -> bool:
    """True if ts is a parseable ISO timestamp string. Used by rule 1 — a bad or
    missing timestamp rejects the event as bad_timestamp.
    """
    if not isinstance(ts, str) or not ts:
        return False
    try:
        # Accept the trailing Z form (Python <3.11 fromisoformat rejects "Z").
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _write_reject(conn: sqlite3.Connection, session_id, raw: Dict[str, Any], reason: str, now: str) -> None:
    """Append one row to the rejects audit log: the original raw event plus the
    closed-enum reason. Used for both dropped events and kept conflicts.
    """
    conn.execute(
        "INSERT INTO rejects (session_id, raw_json, reason, created_at) VALUES (?, ?, ?, ?)",
        (session_id, json.dumps(raw, sort_keys=True), reason, now),
    )


def _upsert_session(conn: sqlite3.Connection, session_id, ts, integrity: int) -> None:
    """Create the session on first sight; widen the ts window and raise the
    integrity flag (never lower it) on subsequent events."""
    row = conn.execute("SELECT first_ts, last_ts, integrity_flag FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sessions (id, first_ts, last_ts, integrity_flag) VALUES (?, ?, ?, ?)",
            (session_id, ts, ts, integrity),
        )
        return
    first_ts = min(row["first_ts"], ts) if row["first_ts"] else ts
    last_ts = max(row["last_ts"], ts) if row["last_ts"] else ts
    flag = 1 if (row["integrity_flag"] or integrity) else 0
    conn.execute(
        "UPDATE sessions SET first_ts = ?, last_ts = ?, integrity_flag = ? WHERE id = ?",
        (first_ts, last_ts, flag, session_id),
    )


def ingest_events(raw_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply the rules in order, per event. Returns the endpoint summary.

    accepted = events inserted into `events` (includes kept conflict rows).
    rejected = events dropped (duplicate / bad_timestamp / missing_target).
               conflicting_step is log-only — the event is kept, so it is NOT
               counted here.
    flagged  = inserted events with injection_flag = 1.
    sessions = session ids that gained at least one accepted event.
    """
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    accepted = rejected = flagged = 0
    touched_sessions: List[str] = []

    try:
        with conn:  # single transaction; in-batch inserts are visible to later SELECTs
            for raw in raw_events:
                session_id = raw.get("session_id")
                step = raw.get("step")
                action = raw.get("action")
                target = raw.get("target")
                observation = raw.get("observation")
                status = raw.get("status")
                ts = raw.get("ts")

                # Rule 1: validate shape.
                if not _valid_ts(ts):
                    _write_reject(conn, session_id, raw, REJECT_BAD_TIMESTAMP, now)
                    rejected += 1
                    continue
                if action in TARGET_REQUIRED_ACTIONS and not target:
                    _write_reject(conn, session_id, raw, REJECT_MISSING_TARGET, now)
                    rejected += 1
                    continue

                # Rule 2: content hash over the semantic fields.
                chash = content_hash(action, target, observation, status)

                # Rule 3: exact dedupe (same session, step, content) -> drop silently.
                exact = conn.execute(
                    "SELECT 1 FROM events WHERE session_id = ? AND step = ? AND content_hash = ?",
                    (session_id, step, chash),
                ).fetchone()
                if exact is not None:
                    _write_reject(conn, session_id, raw, REJECT_DUPLICATE, now)
                    rejected += 1
                    continue

                # Rule 4: conflict (same session+step, different content) -> keep both,
                # flag the session, log a reject row. The event is still inserted.
                same_step = conn.execute(
                    "SELECT 1 FROM events WHERE session_id = ? AND step = ?",
                    (session_id, step),
                ).fetchone()
                is_conflict = same_step is not None
                if is_conflict:
                    _write_reject(conn, session_id, raw, REJECT_CONFLICT, now)

                # Rule 5: injection scan over observation.
                injection = 1 if scan_injection(observation) else 0

                conn.execute(
                    """INSERT INTO events
                       (session_id, step, ts, action, target, observation, status, content_hash, injection_flag)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, step, ts, action, target, observation, status, chash, injection),
                )
                accepted += 1
                if injection:
                    flagged += 1
                _upsert_session(conn, session_id, ts, 1 if is_conflict else 0)
                if session_id not in touched_sessions:
                    touched_sessions.append(session_id)
    finally:
        conn.close()

    return {
        "accepted": accepted,
        "rejected": rejected,
        "flagged": flagged,
        "sessions": touched_sessions,
    }

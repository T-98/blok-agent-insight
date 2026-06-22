"""Insight generation: prompt build, LLM call, schema validation, guard checks,
single retry, deterministic fallback, persistence.

Contract (the brief's Block 3):
  - Features are computed deterministically and fed to the LLM. The LLM only
    narrates — it never computes features or reject reasons.
  - Every call path (valid / retried / fallback) persists exactly one
    insight_runs row, stamped with the prompt version used.
  - Every call regenerates fresh and appends a new row. No caching, no overwrite.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db import get_conn
from app.features import extract_features
from app.guards import cap_confidence, contradiction_guard, groundedness_guard
from app.prompts import ACTIVE_PROMPT, build_prompt
from app.schemas import FrictionPoint, Insight

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1000


# --------------------------------------------------------------------------- #
# Data access helpers (read-only)                                             #
# --------------------------------------------------------------------------- #
def session_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    """True if a session row with this id exists — used to return 404 for unknown
    sessions before doing any work."""
    return conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None


def load_events(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Load the session's cleaned events as dicts, in insertion order, with the
    fields the feature functions and prompt builder need."""
    rows = conn.execute(
        """SELECT step, action, target, observation, status, content_hash, injection_flag
           FROM events WHERE session_id = ? ORDER BY id""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def reject_count(conn: sqlite3.Connection, session_id: str) -> int:
    """How many reject rows this session has — feeds the confidence cap (more
    rejects → lower confidence ceiling)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM rejects WHERE session_id = ?", (session_id,)).fetchone()
    return int(row["n"])


# --------------------------------------------------------------------------- #
# Deterministic Insight builder (fallback)                                    #
# --------------------------------------------------------------------------- #
def _friction_points(features: Dict[str, Any]) -> List[FrictionPoint]:
    """Build the fallback's friction points straight from the deterministic
    friction_events (no LLM) — one point per friction event, description is
    "status: observation"."""
    return [
        FrictionPoint(step=f["step"], description=f"{f['status']}: {f['observation']}")
        for f in features["friction_events"]
    ]


def _templated_summary(session_id: str, total: int, features: Dict[str, Any]) -> str:
    """Build the fallback summary text deterministically from features — event
    count, progress ratio, terminal status, then optional sentences for friction,
    a detected loop, and any flagged injection. No LLM involved."""
    pr = features["progress_ratio"]
    parts = [
        f"Session {session_id}: {total} event(s), progress ratio {pr:.0%}, "
        f"terminal status '{features['terminal_status']}'."
    ]
    if features["friction_events"]:
        detail = "; ".join(
            f"step {f['step']} ({f['status']}): {f['observation']}" for f in features["friction_events"]
        )
        parts.append(f"Friction encountered — {detail}.")
    if features["loop_score"] > 1:
        parts.append(f"Detected a repeated action loop (loop_score={features['loop_score']}).")
    if features["injection_count"] > 0:
        parts.append(
            f"{features['injection_count']} prompt-injection attempt(s) flagged in page content "
            f"(described as content, not followed)."
        )
    return " ".join(parts)


def _fallback_insight(session_id: str, events: List[Dict[str, Any]], features: Dict[str, Any]) -> Insight:
    """Deterministic fallback after two failed LLM attempts (brief Block 3 step 7)."""
    return Insight(
        session_id=session_id,
        summary=_templated_summary(session_id, len(events), features),
        friction_points=_friction_points(features),
        confidence=0.3,
        recommended_follow_up="manual review recommended",
    )


# --------------------------------------------------------------------------- #
# LLM call + validation                                                       #
# --------------------------------------------------------------------------- #
def _call_llm(prompt: str) -> str:
    """One Anthropic call. The SDK is imported lazily so it's only required when
    actually generating an insight. A safety refusal yields empty text, which
    flows into the validation-failure path (retry, then fallback)."""
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in resp.content if b.type == "text")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> str:
    """Strip markdown fences and isolate the outermost JSON object, in case the
    model ignores the no-fences instruction."""
    stripped = _FENCE_RE.sub("", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _validate_and_guard(
    raw: str, session_id: str, features: Dict[str, Any], event_steps: List[int]
) -> Tuple[Optional[Insight], str]:
    """Parse -> schema-validate -> guards 1 & 2. Returns (insight, '') on full
    pass, else (None, failure_message)."""
    try:
        data = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"output was not valid JSON ({exc})"
    try:
        insight = Insight.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        return None, f"output did not match the Insight schema ({exc})"

    ok, msg = contradiction_guard(insight, features)
    if not ok:
        return None, msg
    ok, msg = groundedness_guard(insight, event_steps)
    if not ok:
        return None, msg
    return insight, ""


def _apply_cap(insight: Insight, features: Dict[str, Any], rejects: int) -> Insight:
    """Return a copy of the insight with its confidence lowered by the cap (based
    on injection/conflict/reject counts). Always applied; never a failure."""
    capped = cap_confidence(
        insight.confidence,
        features["injection_count"],
        features["conflict_count"],
        rejects,
    )
    return insight.model_copy(update={"confidence": capped})


def _persist(
    conn: sqlite3.Connection,
    session_id: str,
    version: str,
    model: str,
    raw_output: str,
    insight: Insight,
    status: str,
    latency_ms: int,
) -> None:
    """Append one row to insight_runs recording this generation — version, model,
    the raw model output, the validated insight, the status (valid/retried/
    fallback), and latency. Called on every path; rows are never overwritten."""
    with conn:
        conn.execute(
            """INSERT INTO insight_runs
               (session_id, prompt_version, model, raw_output, validated_output,
                validation_status, latency_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                version,
                model,
                raw_output,
                insight.model_dump_json(),
                status,
                latency_ms,
                datetime.utcnow().isoformat(),
            ),
        )


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def generate_insight(
    session_id: str,
    version: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Insight:
    """Generate one Insight for a session. `version` defaults to ACTIVE_PROMPT
    (the endpoint path); eval.py passes a version explicitly to bypass it."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    version = version or ACTIVE_PROMPT
    t0 = time.perf_counter()
    try:
        events = load_events(conn, session_id)
        features = extract_features(events)
        rejects = reject_count(conn, session_id)
        event_steps = [e["step"] for e in events]

        prompt = build_prompt(version, session_id, events, features)

        # Attempt 1.
        raw = _call_llm(prompt)
        insight, err = _validate_and_guard(raw, session_id, features, event_steps)
        status = "valid"
        final_raw = raw

        # Attempt 2 (single retry) — append the exact failure to the prompt.
        if insight is None:
            retry_prompt = f"{prompt}\n\nYour previous output failed: {err}. Fix it."
            raw = _call_llm(retry_prompt)
            insight, err = _validate_and_guard(raw, session_id, features, event_steps)
            final_raw = raw
            if insight is not None:
                status = "retried_valid"
            else:
                insight = _fallback_insight(session_id, events, features)
                status = "fallback"

        insight = _apply_cap(insight, features, rejects)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        _persist(conn, session_id, version, MODEL, final_raw, insight, status, latency_ms)
        return insight
    finally:
        if own_conn:
            conn.close()

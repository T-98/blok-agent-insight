"""FastAPI app: ingest endpoint + session list/detail + per-session insight."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_conn, init_db
from app.features import extract_features
from app.ingest import ingest_events
from app.insight import generate_insight, load_events, session_exists
from app.schemas import (
    EventOut,
    FeaturesOut,
    IngestSummary,
    Insight,
    RejectOut,
    RunOut,
    SessionSummary,
)

_DATA = Path(__file__).resolve().parents[1] / "data"
_SAMPLE = _DATA / "sample_trajectory.json"
_CONFLICT = _DATA / "conflict_example.json"


def _seed_if_empty() -> None:
    """On a fresh deploy the sqlite file is empty (and ephemeral). When
    SEED_SAMPLE is set, load the sample trajectory plus a conflict example (so
    the integrity/kept-conflict path is demonstrable) so the dashboard has data
    to show. No-op if any session already exists — never clobbers real data."""
    if not os.environ.get("SEED_SAMPLE"):
        return
    conn = get_conn()
    try:
        has_data = conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    finally:
        conn.close()
    if has_data:
        return
    for path in (_SAMPLE, _CONFLICT):
        if path.exists():
            ingest_events(json.loads(path.read_text()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_if_empty()
    yield


app = FastAPI(title="Browser Agent Insight Pipeline", lifespan=lifespan)

# The dashboard is a separate Vite dev server (and any static origin in prod),
# so allow cross-origin GET/POST. Read-only data + ingest only; no credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.post("/trajectories", response_model=IngestSummary)
def post_trajectories(events: List[Dict[str, Any]]) -> IngestSummary:
    """Accept a raw JSON list of events. Returns accepted/rejected/flagged counts
    and the sessions touched. Shape/reject decisions are deterministic (no LLM)."""
    return IngestSummary(**ingest_events(events))


@app.get("/sessions", response_model=List[SessionSummary])
def list_sessions() -> List[SessionSummary]:
    """List every ingested session with deterministic, read-only metadata for
    the dashboard. No LLM call — counts come straight from the tables."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT s.id, s.first_ts, s.last_ts, s.integrity_flag,
                      (SELECT COUNT(*) FROM events e WHERE e.session_id = s.id) AS event_count,
                      (SELECT COALESCE(SUM(e.injection_flag), 0) FROM events e WHERE e.session_id = s.id) AS injection_count,
                      (SELECT COUNT(*) FROM insight_runs r WHERE r.session_id = s.id) AS insight_runs
               FROM sessions s
               ORDER BY s.first_ts"""
        ).fetchall()
        return [SessionSummary(**dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/sessions/{session_id}/events", response_model=List[EventOut])
def get_events(session_id: str) -> List[EventOut]:
    """The session's cleaned trajectory, in step order. Lets the UI show the
    raw agent run alongside the generated insight."""
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        rows = conn.execute(
            """SELECT step, ts, action, target, observation, status, injection_flag
               FROM events WHERE session_id = ? ORDER BY step, id""",
            (session_id,),
        ).fetchall()
        return [EventOut(**dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/sessions/{session_id}/features", response_model=FeaturesOut)
def get_features(session_id: str) -> FeaturesOut:
    """The deterministic features (features.extract_features) the LLM narrates
    over — read-only, no LLM. Lets the UI show the backend's authoritative
    numbers instead of re-deriving them client-side."""
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        return FeaturesOut(**extract_features(load_events(conn, session_id)))
    finally:
        conn.close()


@app.get("/sessions/{session_id}/rejects", response_model=List[RejectOut])
def get_rejects(session_id: str) -> List[RejectOut]:
    """Rows dropped or flagged at ingest (the closed reject enum), so the UI can
    tell the dedupe / kept-conflict story. No LLM in this path."""
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        rows = conn.execute(
            "SELECT id, reason, raw_json, created_at FROM rejects WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [RejectOut(**dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/sessions/{session_id}/runs", response_model=List[RunOut])
def get_runs(session_id: str) -> List[RunOut]:
    """The persisted insight_runs (metadata only — no raw/validated payloads),
    so the UI can show the real validation_status and per-version run history."""
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        rows = conn.execute(
            """SELECT id, prompt_version, model, validation_status, latency_ms, created_at
               FROM insight_runs WHERE session_id = ? ORDER BY id""",
            (session_id,),
        ).fetchall()
        return [RunOut(**dict(r)) for r in rows]
    finally:
        conn.close()


@app.get("/sessions/{session_id}/insight", response_model=Insight)
def get_insight(
    session_id: str,
    version: Optional[str] = Query(
        default=None,
        description="Prompt version to run: 'v1' (plain) or 'v2' (injection-hardened). "
        "Defaults to the server's ACTIVE_PROMPT. Lets the dashboard compare versions.",
    ),
) -> Insight:
    if version is not None and version not in ("v1", "v2"):
        raise HTTPException(status_code=400, detail="version must be 'v1' or 'v2'")
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        return generate_insight(session_id, version=version, conn=conn)
    finally:
        conn.close()

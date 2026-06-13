"""FastAPI app: ingest endpoint + session list/detail + per-session insight."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_conn, init_db
from app.ingest import ingest_events
from app.insight import generate_insight, session_exists
from app.schemas import EventOut, IngestSummary, Insight, SessionSummary

_SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_trajectory.json"


def _seed_if_empty() -> None:
    """On a fresh deploy the sqlite file is empty (and ephemeral). When
    SEED_SAMPLE is set, load the sample trajectory so the dashboard has data to
    show. No-op if any session already exists, so it never clobbers real data."""
    if not os.environ.get("SEED_SAMPLE"):
        return
    conn = get_conn()
    try:
        has_data = conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    finally:
        conn.close()
    if has_data or not _SAMPLE.exists():
        return
    ingest_events(json.loads(_SAMPLE.read_text()))


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


@app.get("/sessions/{session_id}/insight", response_model=Insight)
def get_insight(session_id: str) -> Insight:
    conn = get_conn()
    try:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        return generate_insight(session_id, conn=conn)
    finally:
        conn.close()

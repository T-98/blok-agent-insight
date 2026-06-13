"""Pydantic models. The Insight schema mirrors the brief exactly.

Trade-off: raw inbound events are accepted as plain dicts (not a Pydantic
model) so that ingest can apply the spec's closed-enum reject reasons
(bad_timestamp, missing_required_target) deterministically. A Pydantic model
would raise a generic 422 instead of our auditable reject rows.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class FrictionPoint(BaseModel):
    step: int
    description: str


class Insight(BaseModel):
    session_id: str
    summary: str
    friction_points: List[FrictionPoint]
    confidence: float = Field(..., ge=0.0, le=1.0)  # 0..1
    recommended_follow_up: str


class IngestSummary(BaseModel):
    accepted: int
    rejected: int
    flagged: int
    sessions: List[str]


class SessionSummary(BaseModel):
    """One row for the dashboard session list — read-only, derived from the
    events/sessions tables. No LLM in this path."""
    id: str
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    integrity_flag: int = 0
    event_count: int = 0
    injection_count: int = 0
    insight_runs: int = 0


class EventOut(BaseModel):
    """A single trajectory step, surfaced to the UI so the agent's run is
    auditable next to its insight."""
    step: int
    ts: Optional[str] = None
    action: Optional[str] = None
    target: Optional[str] = None
    observation: Optional[str] = None
    status: Optional[str] = None
    injection_flag: int = 0


class FrictionEvent(BaseModel):
    step: int
    status: Optional[str] = None
    observation: Optional[str] = None


class FeaturesOut(BaseModel):
    """The deterministic features the LLM narrates over — exposed read-only so
    the dashboard shows the *backend's* computed values, not a client re-derive.
    Mirrors features.extract_features exactly."""
    progress_ratio: float
    loop_score: int
    stall_streak: int
    terminal_status: Optional[str] = None
    injection_count: int
    conflict_count: int
    friction_events: List[FrictionEvent]


class RejectOut(BaseModel):
    """One dropped/flagged-at-ingest row, so the UI can tell the dedupe/conflict
    story. `reason` is the closed enum; `raw_json` is the original event."""
    id: int
    reason: str
    raw_json: str
    created_at: Optional[str] = None


class RunOut(BaseModel):
    """One persisted insight_runs row (no raw/validated payloads), so the UI can
    show the real validation_status and the per-version run history."""
    id: int
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    validation_status: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: Optional[str] = None

"""GATE 4 — guard unit tests + an end-to-end assertion on abc123 (LLM mocked)."""
from __future__ import annotations

import re

import pytest

from app.db import get_conn
from app.features import extract_features
from app.guards import cap_confidence, contradiction_guard, groundedness_guard
from app.ingest import ingest_events
from app.insight import generate_insight, load_events
from app.schemas import FrictionPoint, Insight

_SUCCESS_RE = re.compile(r"successfully completed|no issues|went smoothly", re.IGNORECASE)


@pytest.fixture
def abc123(db_path, sample_events):
    ingest_events(sample_events)
    conn = get_conn()
    try:
        events = load_events(conn, "abc123")
        return events, extract_features(events)
    finally:
        conn.close()


def test_contradiction_fails_on_false_success(abc123):
    _events, features = abc123
    fabricated = Insight(
        session_id="abc123",
        summary="The agent successfully completed the task with no issues.",
        friction_points=[],
        confidence=0.9,
        recommended_follow_up="none",
    )
    ok, msg = contradiction_guard(fabricated, features)
    assert ok is False
    assert msg


def test_groundedness_fails_on_nonexistent_step(abc123):
    events, _features = abc123
    insight = Insight(
        session_id="abc123",
        summary="Cited a step that does not exist.",
        friction_points=[FrictionPoint(step=99, description="phantom")],
        confidence=0.5,
        recommended_follow_up="none",
    )
    ok, msg = groundedness_guard(insight, [e["step"] for e in events])
    assert ok is False
    assert "99" in msg


def test_confidence_cap_math():
    # abc123 shape: injection 1, conflict 0, rejects 1 -> cap exactly 0.7
    # (exact, not approx: the round() in cap_confidence must kill the
    # 0.7000000000000001 binary-float artifact, or a <= 0.7 bound breaks).
    assert cap_confidence(0.9, 1, 0, 1) == 0.7
    # llm value below the cap is preserved.
    assert cap_confidence(0.4, 1, 0, 1) == 0.4
    # floored at 0.05 when the penalties exceed 0.95.
    assert cap_confidence(0.9, 5, 0, 0) == 0.05


def test_end_to_end_abc123(db_path, sample_events, mock_llm):
    ingest_events(sample_events)
    insight = generate_insight("abc123")
    assert _SUCCESS_RE.search(insight.summary) is None  # never reads as clean success
    assert insight.confidence <= 0.8  # cap enforced (injection + reject penalties)
    assert insight.friction_points  # blocking/injection signals surfaced

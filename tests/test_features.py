"""GATE 2 — feature extraction over the deduped sample events."""
from __future__ import annotations

import pytest

from app.db import get_conn
from app.features import extract_features
from app.ingest import ingest_events
from app.insight import load_events


@pytest.fixture
def features_by_session(db_path, sample_events):
    ingest_events(sample_events)
    conn = get_conn()
    try:
        return {
            sid: extract_features(load_events(conn, sid))
            for sid in ("abc123", "xyz789", "loop456")
        }
    finally:
        conn.close()


def test_abc123(features_by_session):
    f = features_by_session["abc123"]
    assert f["progress_ratio"] == 0.6  # 3 success / 5 events after dedupe
    assert f["injection_count"] == 1
    assert f["terminal_status"] == "success"
    friction_steps = {fe["step"] for fe in f["friction_events"]}
    assert {3, 4} <= friction_steps


def test_xyz789(features_by_session):
    f = features_by_session["xyz789"]
    friction_steps = {fe["step"] for fe in f["friction_events"]}
    assert 2 in friction_steps
    assert any(fe["status"] == "validation_error" for fe in f["friction_events"])


def test_loop456(features_by_session):
    f = features_by_session["loop456"]
    assert f["loop_score"] == 2
    assert f["stall_streak"] == 2
    assert f["terminal_status"] == "low_progress"

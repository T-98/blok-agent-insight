"""Read endpoints that surface the deterministic backend internals to the
dashboard: features, ingest rejects, and insight_runs. The LLM is mocked, so
these run offline with no API key."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import get_conn

CONFLICT_PATH = Path(__file__).resolve().parents[1] / "data" / "conflict_example.json"


@pytest.fixture(autouse=True)
def _use_mock(mock_llm):
    """Mock the Anthropic call for the whole module (runs endpoint needs it)."""


# ---------------------------------------------------------------- features ---
def test_features_endpoint(client, sample_events):
    client.post("/trajectories", json=sample_events)
    resp = client.get("/sessions/abc123/features")
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["progress_ratio"] == 0.6  # 3/5 after dedupe
    assert d["injection_count"] == 1
    assert d["conflict_count"] == 0
    assert d["terminal_status"] == "success"
    steps = {fe["step"] for fe in d["friction_events"]}
    assert {3, 4} <= steps

    loop = client.get("/sessions/loop456/features").json()
    assert loop["loop_score"] == 2
    assert loop["stall_streak"] == 2
    assert loop["terminal_status"] == "low_progress"


def test_features_404(client):
    assert client.get("/sessions/nope/features").status_code == 404


# ----------------------------------------------------------------- rejects ---
def test_rejects_endpoint(client, sample_events):
    client.post("/trajectories", json=sample_events)
    resp = client.get("/sessions/abc123/rejects")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["reason"] == "duplicate_event"
    assert "step" in rows[0]["raw_json"]  # the dropped raw event is preserved


def test_rejects_404(client):
    assert client.get("/sessions/nope/rejects").status_code == 404


# -------------------------------------------------------------------- runs ---
def test_runs_endpoint(client, sample_events):
    client.post("/trajectories", json=sample_events)
    client.get("/sessions/abc123/insight")  # one mocked, valid run
    resp = client.get("/sessions/abc123/runs")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["validation_status"] == "valid"
    assert rows[0]["model"] == "claude-sonnet-4-6"
    assert rows[0]["prompt_version"]  # stamped with a version


def test_runs_404(client):
    assert client.get("/sessions/nope/runs").status_code == 404


# ------------------------------------------------ conflict / integrity path ---
def test_conflict_example_surfaces_integrity(client):
    """The seeded conflict example must trigger the kept-conflict path end to
    end: same step + different content -> integrity_flag, a conflicting_step
    reject, both rows kept, conflict_count=1 — all visible through the API."""
    data = json.loads(CONFLICT_PATH.read_text())
    sid = data[0]["session_id"]
    client.post("/trajectories", json=data)

    sess = next(s for s in client.get("/sessions").json() if s["id"] == sid)
    assert sess["integrity_flag"] == 1

    feats = client.get(f"/sessions/{sid}/features").json()
    assert feats["conflict_count"] == 1

    rejects = client.get(f"/sessions/{sid}/rejects").json()
    assert any(r["reason"] == "conflicting_step" for r in rejects)

    events = client.get(f"/sessions/{sid}/events").json()
    assert sum(1 for e in events if e["step"] == 2) == 2  # both kept


def test_conflict_not_counted_as_rejected(client):
    """conflicting_step is log-only: the event is kept, so the ingest summary
    must NOT count it as rejected (only the dropped kinds are)."""
    data = json.loads(CONFLICT_PATH.read_text())
    summary = client.post("/trajectories", json=data).json()
    assert summary["accepted"] == 3
    assert summary["rejected"] == 0

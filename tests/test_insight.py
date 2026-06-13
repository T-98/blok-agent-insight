"""GATE 3 — every session returns a 200 schema-valid Insight and writes one
insight_runs row stamped with the active prompt version. The LLM call is mocked
(see the mock_llm fixture) so the gate runs offline with no API key."""
from __future__ import annotations

import pytest

from app.db import get_conn
from app.prompts import ACTIVE_PROMPT
from app.schemas import Insight


@pytest.fixture(autouse=True)
def _mock_llm(mock_llm):
    """Apply the mocked Anthropic call to every test in this module."""


def test_gate3_all_sessions(client, sample_events):
    assert client.post("/trajectories", json=sample_events).status_code == 200

    for sid in ("abc123", "xyz789", "loop456"):
        resp = client.get(f"/sessions/{sid}/insight")
        assert resp.status_code == 200, resp.text
        insight = Insight.model_validate(resp.json())  # schema-valid
        assert insight.session_id == sid

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT session_id, prompt_version, validation_status FROM insight_runs ORDER BY id"
        ).fetchall()
        # One row per call (3 calls), each stamped with the active prompt version.
        assert len(rows) == 3
        assert {r["session_id"] for r in rows} == {"abc123", "xyz789", "loop456"}
        assert all(r["prompt_version"] == ACTIVE_PROMPT for r in rows)
        assert all(r["validation_status"] == "valid" for r in rows)
    finally:
        conn.close()


def test_unknown_session_404(client):
    assert client.get("/sessions/nope/insight").status_code == 404


def test_insight_version_param(client, sample_events):
    """The endpoint accepts ?version=v1|v2 (for the compare view) and stamps the
    insight_runs row with that version; anything else is a 400."""
    client.post("/trajectories", json=sample_events)
    assert client.get("/sessions/abc123/insight?version=v1").status_code == 200
    assert client.get("/sessions/abc123/insight?version=v2").status_code == 200
    assert client.get("/sessions/abc123/insight?version=v9").status_code == 400

    conn = get_conn()
    try:
        versions = [
            r["prompt_version"]
            for r in conn.execute(
                "SELECT prompt_version FROM insight_runs WHERE session_id='abc123' ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert versions == ["v1", "v2"]


def test_regeneration_appends_rows(client, sample_events):
    """Every call appends a fresh row — never cached, never overwritten."""
    client.post("/trajectories", json=sample_events)
    client.get("/sessions/abc123/insight")
    client.get("/sessions/abc123/insight")
    conn = get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM insight_runs WHERE session_id = 'abc123'"
        ).fetchone()["n"]
        assert n == 2
    finally:
        conn.close()

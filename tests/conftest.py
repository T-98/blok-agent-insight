"""Shared fixtures. Each test gets a throwaway sqlite file via DB_PATH so the
real data/insight.db is never touched and tests don't bleed into each other.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_trajectory.json"


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch the one Anthropic call so the suite runs offline, with no API key.

    The fake echoes the prompt's SESSION_ID and returns schema-valid JSON that
    clears both guards (no clean-success phrasing; cites step 1, which always
    exists), so generate_insight exercises the real validate -> guard -> cap ->
    persist path and lands as validation_status="valid"."""

    def fake_call(prompt: str) -> str:
        m = re.search(r"SESSION_ID:\s*(\S+)", prompt)
        sid = m.group(1) if m else "unknown"
        return json.dumps(
            {
                "session_id": sid,
                "summary": (
                    f"Session {sid}: the agent hit friction; any injected page text is "
                    "described as content and was not followed."
                ),
                "friction_points": [{"step": 1, "description": "friction observed"}],
                "confidence": 0.9,
                "recommended_follow_up": "Review the flagged steps.",
            }
        )

    monkeypatch.setattr("app.insight._call_llm", fake_call)
    return fake_call


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(p))
    from app.db import init_db

    init_db()
    return str(p)


@pytest.fixture
def sample_events():
    return json.loads(SAMPLE_PATH.read_text())


@pytest.fixture
def client(db_path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

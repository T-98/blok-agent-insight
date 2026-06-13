"""GATE 1 — ingest the sample data and assert the planted properties exactly."""
from __future__ import annotations

from app.db import get_conn


def test_gate1_ingest_sample(client, sample_events):
    resp = client.post("/trajectories", json=sample_events)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exact counts from the brief.
    assert body["accepted"] == 9
    assert body["rejected"] == 1
    assert body["flagged"] == 1
    assert sorted(body["sessions"]) == ["abc123", "loop456", "xyz789"]

    conn = get_conn()
    try:
        # Exactly one reject: the abc123 step-4 transport duplicate.
        rejects = conn.execute("SELECT session_id, reason FROM rejects").fetchall()
        assert len(rejects) == 1
        assert rejects[0]["reason"] == "duplicate_event"
        assert rejects[0]["session_id"] == "abc123"

        # Exactly one injection-flagged event: abc123 step 5.
        flagged = conn.execute("SELECT session_id, step FROM events WHERE injection_flag = 1").fetchall()
        assert len(flagged) == 1
        assert flagged[0]["session_id"] == "abc123"
        assert flagged[0]["step"] == 5

        # Three sessions, none flagged for integrity (no conflicts in the sample).
        sessions = conn.execute("SELECT id, integrity_flag FROM sessions").fetchall()
        assert len(sessions) == 3
        assert all(s["integrity_flag"] == 0 for s in sessions)

        # 9 accepted events landed in the table.
        n_events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        assert n_events == 9
    finally:
        conn.close()

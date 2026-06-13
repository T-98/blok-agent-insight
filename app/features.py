"""Pure feature extraction: (list_of_events) -> value. No DB, no LLM.

These deterministic features are what the LLM narrates over. We never let the
LLM compute them — that keeps the factual layer auditable and the model's job
narrow (description only). Each event is a mapping with the keys produced by
ingest: step, action, target, observation, status, content_hash, injection_flag.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

FRICTION_STATUSES = {"blocked", "validation_error", "low_progress"}


def progress_ratio(events: List[Dict[str, Any]]) -> float:
    if not events:
        return 0.0
    success = sum(1 for e in events if e["status"] == "success")
    return success / len(events)


def loop_score(events: List[Dict[str, Any]]) -> int:
    """Max repeat count of any (action, target, observation) triple."""
    if not events:
        return 0
    counts = Counter((e["action"], e["target"], e["observation"]) for e in events)
    return max(counts.values())


def stall_streak(events: List[Dict[str, Any]]) -> int:
    """Longest run of consecutive low_progress events, in step order."""
    best = current = 0
    for e in sorted(events, key=lambda x: x["step"]):
        if e["status"] == "low_progress":
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def terminal_status(events: List[Dict[str, Any]]):
    """Status of the highest step. Ties (a conflict at the top step) resolve to
    the last one in event order, which is deterministic for our insertion."""
    if not events:
        return None
    return max(events, key=lambda e: e["step"])["status"]


def injection_count(events: List[Dict[str, Any]]) -> int:
    return sum(1 for e in events if e.get("injection_flag"))


def conflict_count(events: List[Dict[str, Any]]) -> int:
    """Number of steps that carry more than one distinct content_hash."""
    by_step: Dict[Any, set] = {}
    for e in events:
        by_step.setdefault(e["step"], set()).add(e["content_hash"])
    return sum(1 for hashes in by_step.values() if len(hashes) > 1)


def friction_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(step, status, observation) for every friction-status event, in step order."""
    return [
        {"step": e["step"], "status": e["status"], "observation": e["observation"]}
        for e in sorted(events, key=lambda x: x["step"])
        if e["status"] in FRICTION_STATUSES
    ]


def extract_features(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "progress_ratio": progress_ratio(events),
        "loop_score": loop_score(events),
        "stall_streak": stall_streak(events),
        "terminal_status": terminal_status(events),
        "injection_count": injection_count(events),
        "conflict_count": conflict_count(events),
        "friction_events": friction_events(events),
    }

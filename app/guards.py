"""Output guards. Run after schema validation. Pure functions over the parsed
Insight + deterministic features — no LLM calls here.

Guards 1-2 are pass/fail and feed the retry loop. Guard 3 (confidence cap) is
applied silently to the value and is never a failure.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.schemas import Insight

# Phrases that read as a clean-success claim.
_SUCCESS_PATTERNS = [
    re.compile(r"successfully completed", re.IGNORECASE),
    re.compile(r"no issues", re.IGNORECASE),
    re.compile(r"went smoothly", re.IGNORECASE),
]


def contradiction_guard(insight: Insight, features: Dict[str, Any]) -> Tuple[bool, str]:
    """Fail if the session clearly hit trouble (terminal blocked, injection, or
    any blocked event) yet the summary claims clean success with no friction
    points listed."""
    triggered = (
        features.get("terminal_status") == "blocked"
        or features.get("injection_count", 0) > 0
        or any(fe["status"] == "blocked" for fe in features.get("friction_events", []))
    )
    if not triggered:
        return True, ""
    claims_success = any(p.search(insight.summary) for p in _SUCCESS_PATTERNS)
    if claims_success and not insight.friction_points:
        return (
            False,
            "Summary reads as clean success, but the session has blocking/injection "
            "signals and no friction_points were listed.",
        )
    return True, ""


def groundedness_guard(insight: Insight, event_steps: List[int]) -> Tuple[bool, str]:
    """Every cited friction step must exist among the session's event steps."""
    valid = set(event_steps)
    for fp in insight.friction_points:
        if fp.step not in valid:
            return (
                False,
                f"friction_points cites step {fp.step}, which does not exist in the session "
                f"(valid steps: {sorted(valid)}).",
            )
    return True, ""


def cap_confidence(
    llm_confidence: float,
    injection_count: int,
    conflict_count: int,
    reject_count_for_session: int,
) -> float:
    """final = min(llm, 1 - 0.2*injection - 0.2*conflict - 0.1*rejects), floored at 0.05.
    Applied silently after generation — not a failure."""
    cap = 1.0 - 0.2 * injection_count - 0.2 * conflict_count - 0.1 * reject_count_for_session
    # round() kills binary float error: 1.0 - 0.2 - 0.1 is 0.7000000000000001,
    # which would store an ugly confidence and break a <= 0.7 bound. The
    # penalties are 0.1-granular, so 4 dp is exact for any real input.
    return round(max(0.05, min(llm_confidence, cap)), 4)

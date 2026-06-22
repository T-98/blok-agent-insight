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
    """Catch the model claiming everything went fine when it clearly didn't.

    This guard only kicks in if the session actually had trouble: it ended
    blocked, or it had an injection flag, or some event was blocked. If the
    session was clean, the guard always passes and we stop here.

    When it did have trouble, we look at the LLM's summary text:

      - FAIL: the summary says something like "successfully completed" AND the
        model listed no friction points at all. That's the model glossing over
        a session we know went wrong, so we fail it and force a retry.
        e.g. abc123 hit a CAPTCHA + an injection, but the summary reads
        "The agent successfully completed the task." with no friction listed.

      - PASS: the summary describes the trouble, or it lists friction points.
        e.g. "The agent was blocked by a CAPTCHA" with step 4 in friction_points.
    """
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
    """Make sure every step the model points to is a real step in the session.

    The model returns friction points that each cite a step number. We check
    each one against the steps that actually exist. abc123 has steps 1-5, so:

      - PASS: the model cites step 4 — that step exists.
      - FAIL: the model cites step 99 — no such step, so the model made it up.
        We fail and force a retry.
    """
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
    """Lower the model's confidence when the session had problems we don't trust
    it to weigh on its own. Each problem costs some confidence: an injection
    costs 0.2, a conflict costs 0.2, each reject costs 0.1. We subtract those
    from 1.0 to get a ceiling, then take whichever is lower — the model's number
    or that ceiling. Never goes below 0.05. This always runs; it's never a failure.

      - abc123 had 1 injection and 1 reject, so the ceiling is
        1 - 0.2 - 0.1 = 0.7. Even if the model said 0.9, we cap it to 0.7.
      - xyz789 had no problems, so the ceiling is 1.0 and we keep the model's
        number, e.g. 0.6 stays 0.6.
      - If enough problems push the ceiling near or below zero, the 0.05 floor
        keeps confidence from going negative.
    """
    cap = 1.0 - 0.2 * injection_count - 0.2 * conflict_count - 0.1 * reject_count_for_session
    # round() kills binary float error: 1.0 - 0.2 - 0.1 is 0.7000000000000001,
    # which would store an ugly confidence and break a <= 0.7 bound. The
    # penalties are 0.1-granular, so 4 dp is exact for any real input.
    return round(max(0.05, min(llm_confidence, cap)), 4)

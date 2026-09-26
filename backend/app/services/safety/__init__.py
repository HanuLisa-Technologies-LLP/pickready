"""Protecting the agent pipeline: content screening and action gates.

Each layer is the net under a rule rather than a replacement for it.
Candidate text is meant to be data, and `content` applies that to retrieved
chunks too. Agents are meant to hold no write tool, and `actions` says which
decisions stay human even so.

The PII masker (`pii`) was DELETED in the stage 3 final sweeps: nothing in the
product called it, only an eval regression case and two tests, and a masker
nothing runs is a protection that exists in prose only. Traces carry
identifiers, counts and timings (`observability.trace._SAFE_STAGE_KEYS`),
which is the rule it was the net under.
"""
from __future__ import annotations

from app.services.safety import actions, content
from app.services.safety.actions import (
    AUTONOMY_CONFIDENCE_FLOOR,
    SENSITIVE_ACTIONS,
    Decision,
    evaluate,
)
from app.services.safety.content import ScreenResult, screen_chunks, screen_text

__all__ = [
    "AUTONOMY_CONFIDENCE_FLOOR",
    "Decision",
    "SENSITIVE_ACTIONS",
    "ScreenResult",
    "actions",
    "content",
    "evaluate",
    "screen_chunks",
    "screen_text",
]

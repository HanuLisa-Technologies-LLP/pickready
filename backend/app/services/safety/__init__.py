"""Protecting the agent pipeline: PII masking, content screening, action gates.

Three layers, each the net under a rule rather than a replacement for it. Logs
are meant to carry no content, and `pii` masks what slips through. Candidate
text is meant to be data, and `content` applies that to retrieved chunks too.
Agents are meant to hold no write tool, and `actions` says which decisions stay
human even so.
"""
from __future__ import annotations

from app.services.safety import actions, content, pii
from app.services.safety.actions import (
    AUTONOMY_CONFIDENCE_FLOOR,
    SENSITIVE_ACTIONS,
    Decision,
    evaluate,
)
from app.services.safety.content import ScreenResult, screen_chunks, screen_text
from app.services.safety.pii import contains_pii, mask, mask_text

__all__ = [
    "AUTONOMY_CONFIDENCE_FLOOR",
    "Decision",
    "SENSITIVE_ACTIONS",
    "ScreenResult",
    "actions",
    "contains_pii",
    "content",
    "evaluate",
    "mask",
    "mask_text",
    "pii",
    "screen_chunks",
    "screen_text",
]

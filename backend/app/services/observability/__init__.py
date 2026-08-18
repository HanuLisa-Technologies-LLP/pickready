"""Visibility into what an agent did: traces, failure categories, latency SLAs.

The rule that shapes all of it: identifiers, counts and timings cross this
boundary, and content never does. A trace row is far more widely readable than
a LangSmith project, and prompts carry a real candidate's answers.
"""
from __future__ import annotations

from app.services.observability import sla, trace
from app.services.observability.sla import assess, compliance, percentile
from app.services.observability.trace import (
    RCA_AUTHORIZATION,
    RCA_BUDGET,
    RCA_PROMPT_QUALITY,
    RCA_PROVIDER,
    RCA_RETRIEVAL_QUALITY,
    RCA_TIMEOUT,
    RCA_TOOL_OUTPUT,
    RCA_UNKNOWN,
    RequestTrace,
    categorise,
    persist,
)

__all__ = [
    "RCA_AUTHORIZATION",
    "RCA_BUDGET",
    "RCA_PROMPT_QUALITY",
    "RCA_PROVIDER",
    "RCA_RETRIEVAL_QUALITY",
    "RCA_TIMEOUT",
    "RCA_TOOL_OUTPUT",
    "RCA_UNKNOWN",
    "RequestTrace",
    "assess",
    "categorise",
    "compliance",
    "percentile",
    "persist",
    "sla",
    "trace",
]

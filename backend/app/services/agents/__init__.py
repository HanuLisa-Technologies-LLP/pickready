"""The six named agents: identity, envelope, A2A artifacts, gates, escalation.

Re-exports only. Nothing here decides anything: a package `__init__` that
carried logic would run at import time for every caller that only wanted one
symbol, and the identity table is deliberately validated by a test rather than
at import.
"""
from __future__ import annotations

from app.services.agents import artifacts, envelope, escalation, gates, identity
from app.services.agents.artifacts import Artifact, ArtifactContractError
from app.services.agents.envelope import Envelope, RunBudget
from app.services.agents.escalation import Escalation
from app.services.agents.identity import AGENTS, agent_card, agent_cards, get

__all__ = [
    "AGENTS",
    "Artifact",
    "ArtifactContractError",
    "Envelope",
    "Escalation",
    "RunBudget",
    "agent_card",
    "agent_cards",
    "artifacts",
    "envelope",
    "escalation",
    "gates",
    "get",
    "identity",
]

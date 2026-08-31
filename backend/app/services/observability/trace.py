"""One trace per agent run: stages, timings, cost, defects, and why it failed.

WHAT THIS IS FOR, CONCRETELY
----------------------------
On 2026-08-04 every deploy was green and three features did not work. The gap
was that nothing recorded what an agent actually DID -- only that the service
answered HTTP. A trace answers the questions a green pipeline cannot: did the
planner take the fast path, did retrieval return anything, how many attempts did
the loop spend, what did the verifier reject, and what did it cost.

THE CORRELATION ID IS CARRIED, NOT PERSISTED, AND THAT IS STATED HONESTLY
--------------------------------------------------------------------------
spec-doc6 4.1 requires the flow's correlation id in "every audit row and log
line". `audit_log.correlation_id` (migration 0061) is the durable half and is
already there. This module supplies the log half: `correlation_id` is on the
trace, appears in `log()` and in `as_dict()`.

`agent_execution_traces` has NO correlation column, so `persist` does not write
one. That is a gap and it is written down rather than papered over by stuffing
the id into `stages`, which would put it somewhere no query looks and let this
docstring claim persistence it does not have. The audit trail, not the trace
table, is where a flow is reconstructed months later.

CONTENT NEVER CROSSES THIS MODULE
----------------------------------
Stage names, statuses, millisecond counts, token counts, typed defects. No
prompt, no answer, no remark. A defect is an instruction ("return exactly 5
items"), which is safe; the text it was produced from is not, and the two are
easy to conflate when adding a field in a hurry. `_SAFE_STAGE_KEYS` is the
allowlist that makes conflating them fail loudly instead of leaking.

PERSISTENCE NEVER BREAKS THE RUN
---------------------------------
`persist` swallows its own failures. A trace is an observation of work that has
already happened; failing the work because the observation could not be written
would make the observability system the least reliable component in the request
path, which is exactly backwards.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.llm_providers import estimate_cost_usd
from app.models.agent import STATUS_FAILED, STATUS_PARTIAL, STATUS_SUCCESS

logger = logging.getLogger(__name__)

#: Everything a stage record may carry. An unknown key is dropped rather than
#: stored: the next person adding "the prompt we sent" to a stage for debugging
#: should find it absent, not find it in the database a month later.
_SAFE_STAGE_KEYS = frozenset(
    {"stage", "status", "duration_ms", "count", "attempts", "detail_code", "degraded"}
)

# ── Failure categories (System 9's RCA) ──────────────────────────────────────
# Deliberately few. A taxonomy with twenty categories is one nobody uses
# consistently, and the point is to be able to say "70% of failures are
# retrieval quality" without arguing about the boundary.
RCA_PROMPT_QUALITY = "prompt_quality"
RCA_RETRIEVAL_QUALITY = "retrieval_quality"
RCA_TOOL_OUTPUT = "tool_output"
RCA_TIMEOUT = "timeout"
RCA_AUTHORIZATION = "authorization"
RCA_PROVIDER = "provider"
RCA_BUDGET = "budget"
RCA_UNKNOWN = "unknown"

#: Defect type or error substring to category. First match wins, so the more
#: specific entries come first.
_RCA_RULES: tuple[tuple[str, str], ...] = (
    ("permission", RCA_AUTHORIZATION),
    ("toolpermission", RCA_AUTHORIZATION),
    ("timeout", RCA_TIMEOUT),
    ("timed out", RCA_TIMEOUT),
    ("deadline", RCA_TIMEOUT),
    ("tooloutput", RCA_TOOL_OUTPUT),
    ("bad_output", RCA_TOOL_OUTPUT),
    ("budget", RCA_BUDGET),
    ("cost", RCA_BUDGET),
    ("llmunavailable", RCA_PROVIDER),
    ("provider", RCA_PROVIDER),
    ("no_context", RCA_RETRIEVAL_QUALITY),
    ("not_grounded", RCA_RETRIEVAL_QUALITY),
    ("retrieval", RCA_RETRIEVAL_QUALITY),
    ("generic_language", RCA_PROMPT_QUALITY),
    ("word_count", RCA_PROMPT_QUALITY),
    ("banned_phrase", RCA_PROMPT_QUALITY),
    ("similarity", RCA_PROMPT_QUALITY),
    ("missing_", RCA_PROMPT_QUALITY),
)


def categorise(defects: list[dict[str, Any]], error: str | None = None) -> str:
    """Map what went wrong onto one of the eight categories.

    Reads the DEFECT TYPE first and the error string second, because a defect is
    structured and an error message is prose somebody may reword.
    """
    haystacks = [str(defect.get("type", "")).casefold() for defect in defects]
    haystacks += [str(defect.get("location", "")).casefold() for defect in defects]
    if error:
        haystacks.append(str(error).casefold())

    for needle, category in _RCA_RULES:
        if any(needle in haystack for haystack in haystacks):
            return category
    return RCA_UNKNOWN


@dataclass
class Stage:
    name: str
    started: float = field(default_factory=time.monotonic)
    duration_ms: int = 0
    status: str = "ok"
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "count": self.count,
        }


@dataclass
class RequestTrace:
    """The accumulator an agent run writes into as it goes."""

    agent_type: str
    task_type: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    #: The flow this run belongs to (spec-doc6 4.1). NOT defaulted and not
    #: minted here: a trace that invented its own correlation id would put a
    #: plausible value on every log line and join to no audit row, which is
    #: worse than an absent one because an absent one is visibly absent.
    #: `provenance.correlation_for_job` is where a real one comes from.
    correlation_id: str | None = None
    tenant_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    complexity: str | None = None
    fast_path: bool = False
    attempts: int = 0
    degraded: bool = False
    confidence: float | None = None
    generated_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    stages: list[dict[str, Any]] = field(default_factory=list)
    defects: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    _started: float = field(default_factory=time.monotonic)
    _open: Stage | None = None

    # ── stage recording ──────────────────────────────────────────────────────

    def start(self, name: str) -> None:
        """Open a stage, closing any stage still open.

        Auto-closing rather than raising: a stage left open by an exception is
        information about where the run died, and losing it to a second
        exception is the least useful possible outcome.
        """
        if self._open is not None:
            self.end(status="interrupted")
        self._open = Stage(name)

    def end(self, *, status: str = "ok", count: int = 0) -> None:
        if self._open is None:
            return
        self._open.duration_ms = int((time.monotonic() - self._open.started) * 1000)
        self._open.status = status
        self._open.count = count
        self.stages.append(self._open.as_dict())
        self._open = None

    def note(self, **fields: Any) -> None:
        """Record an out-of-band stage record, filtered through the allowlist."""
        self.stages.append({k: v for k, v in fields.items() if k in _SAFE_STAGE_KEYS})

    # ── accumulation ─────────────────────────────────────────────────────────

    def add_cost(self, provider: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.cost_usd = round(
            self.cost_usd + estimate_cost_usd(provider, prompt_tokens, completion_tokens), 6
        )

    def add_defects(self, defects: Any) -> None:
        for defect in defects or ():
            if isinstance(defect, dict):
                self.defects.append(
                    {
                        "type": str(defect.get("type", "")),
                        "location": str(defect.get("location", "")),
                    }
                )
            else:
                # `agent_loop.Defect`. Its `detail` is an instruction to a model
                # and is deliberately NOT stored: it can quote the output.
                self.defects.append(
                    {
                        "type": str(getattr(defect, "type", "")),
                        "location": str(getattr(defect, "location", "")),
                    }
                )

    # ── outcome ──────────────────────────────────────────────────────────────

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    @property
    def status(self) -> str:
        if self.error:
            return STATUS_FAILED
        if self.degraded:
            return STATUS_PARTIAL
        return STATUS_SUCCESS

    @property
    def failure_category(self) -> str | None:
        if self.status == STATUS_SUCCESS:
            return None
        return categorise(self.defects, self.error)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "agent_type": self.agent_type,
            "task_type": self.task_type,
            "status": self.status,
            "complexity": self.complexity,
            "fast_path": self.fast_path,
            "attempts": self.attempts,
            "degraded": self.degraded,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "generated_tokens": self.generated_tokens,
            "cost_usd": self.cost_usd,
            "tool_calls": self.tool_calls,
            "stages": self.stages,
            "defects": self.defects,
            "failure_category": self.failure_category,
        }

    def log(self) -> None:
        """One structured line per run, queryable without a database.

        Key=value rather than JSON: it stays greppable in a terminal and every
        log backend this product uses parses it. Content is absent by
        construction because every field here is an identifier or a number.
        """
        logger.info(
            "agent.run request_id=%s correlation_id=%s agent=%s task=%s status=%s "
            "complexity=%s fast_path=%s attempts=%d degraded=%s duration_ms=%d "
            "tokens=%d cost_usd=%.6f tools=%d rca=%s",
            self.request_id,
            self.correlation_id or "-",
            self.agent_type,
            self.task_type,
            self.status,
            self.complexity,
            self.fast_path,
            self.attempts,
            self.degraded,
            self.duration_ms,
            self.generated_tokens,
            self.cost_usd,
            self.tool_calls,
            self.failure_category or "-",
        )


async def persist(session: AsyncSession, trace: RequestTrace) -> bool:
    """Write the trace. Never raises; returns whether the row landed.

    Swallowing is correct here and nowhere else: the work is already done, and
    failing it because its observation could not be written would make
    observability the least reliable thing in the request path.
    """
    import json

    try:
        await session.execute(
            text(
                """
                INSERT INTO agent_execution_traces (
                    request_id, tenant_id, agent_type, task_type, job_id, link_id,
                    status, complexity, fast_path, attempts, degraded, confidence,
                    duration_ms, generated_tokens, cost_usd, tool_calls,
                    stages, defects, failure_category
                ) VALUES (
                    :request_id, :tenant_id, :agent_type, :task_type, :job_id, :link_id,
                    :status, :complexity, :fast_path, :attempts, :degraded, :confidence,
                    :duration_ms, :generated_tokens, :cost_usd, :tool_calls,
                    CAST(:stages AS jsonb), CAST(:defects AS jsonb), :failure_category
                )
                """
            ),
            {
                "request_id": trace.request_id,
                "tenant_id": str(trace.tenant_id) if trace.tenant_id else None,
                "agent_type": trace.agent_type,
                "task_type": trace.task_type,
                "job_id": str(trace.job_id) if trace.job_id else None,
                "link_id": str(trace.link_id) if trace.link_id else None,
                "status": trace.status,
                "complexity": trace.complexity,
                "fast_path": trace.fast_path,
                "attempts": trace.attempts,
                "degraded": trace.degraded,
                "confidence": trace.confidence,
                "duration_ms": trace.duration_ms,
                "generated_tokens": trace.generated_tokens,
                "cost_usd": trace.cost_usd,
                "tool_calls": trace.tool_calls,
                "stages": json.dumps(trace.stages),
                "defects": json.dumps(trace.defects),
                "failure_category": trace.failure_category,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("agent.trace_persist_failed err=%s", type(exc).__name__)
        return False

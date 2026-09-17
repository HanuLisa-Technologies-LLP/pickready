"""The one path a tool is called through: permit, validate, cache, bound, count.

ORDER IS PART OF THE CONTRACT
-----------------------------
  1. resolve      an unknown name fails before anything else happens
  2. permit       the POLICY ENGINE: agent capability, tenant scope, object
                  scope, workflow stage, risk class. Refused BEFORE the payload
                  is even parsed, so a tool an agent does not hold cannot be
                  probed for its schema, and an object another tenant owns
                  cannot be probed for its existence
  3. validate in  a bad payload never reaches a handler
  4. cache read   idempotent tools only, and the key carries the TENANT: this
                  read happens before the handler and therefore before the RLS
                  session, so it is the one step that could hand tenant B a
                  result computed for tenant A
  5. attempt      bounded per attempt AND in total; the deadline PREDICTS
  6. validate out a handler's bad shape is the tool's defect, not the caller's
  7. cache write  after validation, so a malformed result is never memoised
  8. count        name, agent, status, timing. Never payloads.

THE DEADLINE PREDICTS THE NEXT ATTEMPT
--------------------------------------
`elapsed >= deadline` is the check that sounds right and is not, and this
codebase has the scar: one attempt bounded at 24s under a 26s deadline passes
`24 >= 26` as False, starts a second attempt, and the real worst case is 48
seconds with somebody watching a text box. The check here is the same one
`agent_loop` settled on, `elapsed + longest_attempt_so_far >= deadline`, so an
attempt that cannot FINISH inside the budget is never started, and a timed out
attempt's duration counts, because a timeout is the slowest and most
informative thing that can happen.

WHY THIS RAISES
---------------
`agent_loop.run_loop` never raises and is where degradation is decided. A tool
that swallowed its failure would hand its caller an empty shape
indistinguishable from a legitimate empty result, and the caller would render
it. So the split is: tools are correct or they say so, loops decide what a
person sees.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.services.coalescing import TenantScopeMissing
from app.services.tools import approvals, policy, registry, telemetry
from app.services.tools.errors import (
    ToolApprovalRequired,
    ToolError,
    ToolExecutionError,
    ToolInputError,
    ToolNotFound,
    ToolOutputError,
    ToolPermissionError,
    ToolPolicyError,
    ToolScopeError,
    ToolTimeout,
    is_retryable,
)

# Backoff before a retry. Short and fixed rather than exponential: a tool call
# is a local database read, not a third-party API, and the retry exists for a
# dropped connection rather than for a rate limit. The router owns provider
# backoff, which is where exponential belongs.
_RETRY_BACKOFF_SECONDS = 0.25


@dataclass(frozen=True)
class ToolResult:
    """What the caller gets back, and how it got there.

    `cached` and `attempts` are on the result rather than only in telemetry
    because a caller reasoning about its own latency budget needs them, and
    because a test asserting "the second call did not reach the handler" should
    not have to read a counter to find out.
    """

    tool: str
    value: BaseModel
    cached: bool = False
    attempts: int = 0
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.value.model_dump(mode="json")


def _cache_key(tool: str, payload: BaseModel, tenant_id: Any) -> str:
    """Stable key over the TENANT and the VALIDATED input.

    Keyed on the validated model rather than the raw payload so two callers
    that spell the same call differently, a UUID object and its string, or a
    field left to its default, share one entry instead of quietly halving the
    hit rate.

    THE TENANT IS PART OF THE KEY AND IS NOT OPTIONAL
    -------------------------------------------------
    The cache is read at step 4, BEFORE the handler and therefore before the
    RLS-aware session that would have refused the row. That makes this the one
    place in the layer where a call scoped to tenant B can be served tenant A's
    result: the policy engine has already said yes, and nothing downstream ever
    runs to disagree. Every cached tool today takes a globally unique primary
    key, so a COLLISION was never the exposure; cross-tenant SERVING was, and
    only for as long as two tenants shared an input shape.

    The tenant appears twice on purpose. As a key SEGMENT so an operator
    reading Redis can see whose entry an item is without decoding a digest, and
    inside the DIGEST so the segment cannot be spoofed by a tool name that
    carries the separator.

    Refused rather than defaulted, and `coalescing.TenantScopeMissing` is
    imported rather than restated because a second name for one failure is a
    second thing to grep for. A builder that raises cannot be evaded by a
    caller that forgot; a sweep over the source can.

    Provenance: RPN-AI-UP-001 W4.3 ("every cache key contains the tenant id"),
    recorded as a tracked gap in `tests/test_cache_tenant_keying.py` until now.
    """
    scope = "" if tenant_id is None else str(tenant_id).strip()
    if not scope:
        raise TenantScopeMissing(
            f"a tool result cache key for {tool!r} needs a tenant id; the "
            f"cache is read before the RLS session, so a key without one can "
            f"serve one tenant's result to another"
        )
    canonical = json.dumps(
        {"tenant": scope, "input": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return cache.key("tool", tool, scope, digest)


#: Which refusal each policy reason becomes. DATA, so the error class a rule
#: produces is decided beside the rule rather than by a chain of `if reason
#: ==` in the executor, and an unmapped reason raises the generic policy error
#: rather than quietly becoming a permission error.
_REFUSALS: dict[str, type[ToolPolicyError]] = {
    "agent_does_not_hold_tool": ToolPermissionError,
}


def _refusal_for(tool: str, verdict: policy.PolicyVerdict) -> ToolPolicyError:
    """Turn a verdict into the exception the caller sees.

    Cross-tenant is keyed on the STATUS rather than on the reason string,
    because the reason names the object kind and a future rule that also
    answers 404 must get the 404-shaped error without anybody remembering to
    add it here.
    """
    if verdict.decision is policy.PolicyDecision.REQUIRE_APPROVAL:
        return ToolApprovalRequired(
            tool, "a human approval is required for this call", reason=verdict.reason
        )
    if verdict.http_status == 404:
        # No identifier, and no admission that anything was found.
        return ToolScopeError(tool, "no such object", reason=verdict.reason)
    return _REFUSALS.get(verdict.reason, ToolPolicyError)(
        tool, verdict.reason, reason=verdict.reason
    )


async def execute(
    tool: str,
    agent: str,
    payload: dict[str, Any] | BaseModel | None = None,
    *,
    session: AsyncSession | None = None,
    context: policy.ToolContext = policy.UNSCOPED,
) -> ToolResult:
    """Call `tool` as `agent`, for the tenant and objects `context` names.

    Raises a `ToolError` subclass on any failure. `context` defaults to
    `policy.UNSCOPED`, which names no tenant and no object: a caller that
    declares nothing reaches nothing THROUGH THIS LAYER, and the handler still
    reads through the RLS-aware session that is the real boundary. It is a
    module singleton rather than a None with a branch behind it, because two
    paths through one decision is how the second one stops being checked.
    """
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    def refuse(error: ToolError) -> ToolError:
        telemetry.record(
            tool=tool,
            agent=agent,
            status=telemetry.STATUS_REFUSED,
            elapsed_ms=elapsed_ms(),
        )
        return error

    spec = registry.get(tool)
    if spec is None:
        raise refuse(ToolNotFound(tool, "no tool is registered under this name"))

    # ── The policy engine, before anything reads anything ────────────────────
    #
    # The tenant's own approval rules are resolved first, because `evaluate` is
    # pure and the caller does the one database read. That read is about the
    # TENANT and never about the object, so it leaks nothing an unauthorised
    # caller could not already state.
    if session is not None and context.tenant_id is not None:
        context = replace(
            context,
            tenant_approval_required=await approvals.required_risk_classes(
                session, context.tenant_id
            ),
        )
    elif spec.risk is not policy.RiskClass.READ and context.tenant_id is not None:
        # A call that changes something, for a tenant whose configured approval
        # requirements cannot be read. Refused rather than run on the baseline:
        # a policy input that could not be read is not a policy decision.
        raise refuse(
            ToolPolicyError(
                tool,
                "tenant approval rules cannot be resolved without a session",
                reason="approval_rules_unresolvable",
            )
        )

    verdict = policy.evaluate(
        tool=tool, agent=agent, risk=spec.risk, context=context
    )
    telemetry.record_policy(
        tool=tool, agent=agent, verdict=verdict, context=context
    )
    if not verdict.allowed:
        raise refuse(_refusal_for(tool, verdict))

    try:
        parsed = (
            payload
            if isinstance(payload, spec.input_model)
            else spec.input_model.model_validate(payload or {})
        )
    except ValidationError as exc:
        raise refuse(ToolInputError(tool, _terse(exc))) from exc

    if spec.needs_session and session is None:
        raise refuse(ToolInputError(tool, "this tool requires a database session"))

    # A call that declares NO tenant is not cached at all. The alternative is a
    # bucket shared by every caller that declared nothing, which is the same
    # cross-tenant serving one line down in a shape nobody would read as one.
    # Declining to memoise is always correct; it costs the second call its
    # handler and nothing else.
    cache_key = (
        _cache_key(tool, parsed, context.tenant_id)
        if spec.idempotent
        and spec.cache_ttl_seconds > 0
        and context.tenant_id is not None
        else None
    )
    if cache_key is not None:
        hit = await cache.get(cache_key)
        if hit is not None:
            try:
                value = spec.output_model.model_validate(hit)
            except ValidationError:
                # An entry written by an older shape of this tool. Drop it and
                # take the slow path rather than failing a live call over a
                # deploy that renamed a field.
                await cache.invalidate(cache_key)
            else:
                telemetry.record(
                    tool=tool,
                    agent=agent,
                    status=telemetry.STATUS_CACHED,
                    elapsed_ms=elapsed_ms(),
                )
                return ToolResult(
                    tool=tool,
                    value=value,
                    cached=True,
                    attempts=0,
                    elapsed_ms=elapsed_ms(),
                )

    value, attempts = await _attempt_until_bounded(
        spec=spec, agent=agent, parsed=parsed, session=session, started=started
    )

    if cache_key is not None:
        await cache.set(
            cache_key, value.model_dump(mode="json"), ttl=spec.cache_ttl_seconds
        )

    telemetry.record(
        tool=tool,
        agent=agent,
        status=telemetry.STATUS_OK,
        elapsed_ms=elapsed_ms(),
        attempts=attempts,
    )
    return ToolResult(
        tool=tool, value=value, cached=False, attempts=attempts, elapsed_ms=elapsed_ms()
    )


async def _attempt_until_bounded(
    *,
    spec: registry.ToolSpec,
    agent: str,
    parsed: BaseModel,
    session: AsyncSession | None,
    started: float,
) -> tuple[BaseModel, int]:
    """Attempt loop, bounded by count AND by a predicting wall clock."""
    attempts = 0
    longest_attempt = 0.0
    last: BaseException | None = None

    while attempts < spec.max_attempts:
        elapsed = time.monotonic() - started
        # Never START an attempt that cannot finish inside the budget.
        if attempts and elapsed + longest_attempt >= spec.deadline_seconds:
            break

        attempts += 1
        attempt_started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                _invoke(spec, parsed, session), timeout=spec.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            longest_attempt = max(longest_attempt, time.monotonic() - attempt_started)
            last = ToolTimeout(spec.name, f"timed out after {spec.timeout_seconds:g}s")
            last.__cause__ = exc
        except ToolError as exc:
            longest_attempt = max(longest_attempt, time.monotonic() - attempt_started)
            last = exc
        except Exception as exc:  # noqa: BLE001 -- classified below, never swallowed
            longest_attempt = max(longest_attempt, time.monotonic() - attempt_started)
            wrapped = ToolExecutionError(spec.name, f"{type(exc).__name__}: {exc}")
            wrapped.__cause__ = exc
            last = wrapped
            if not is_retryable(exc):
                break
        else:
            try:
                return _validated_output(spec, result), attempts
            except ToolOutputError:
                # Deterministic: the handler builds the same bad shape from the
                # same inputs, so this never buys a retry.
                telemetry.record(
                    tool=spec.name,
                    agent=agent,
                    status=telemetry.STATUS_BAD_OUTPUT,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempts,
                )
                raise

        if last is not None and not is_retryable(last):
            break
        if attempts < spec.max_attempts:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

    failure = last or ToolExecutionError(
        spec.name, "no attempt could finish inside the deadline"
    )
    telemetry.record(
        tool=spec.name,
        agent=agent,
        status=(
            telemetry.STATUS_TIMEOUT
            if isinstance(failure, ToolTimeout)
            else telemetry.STATUS_ERROR
        ),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        attempts=attempts,
    )
    raise failure


async def _invoke(
    spec: registry.ToolSpec, parsed: BaseModel, session: AsyncSession | None
) -> Any:
    if spec.needs_session:
        return await spec.handler(parsed, session=session)
    return await spec.handler(parsed)


def _validated_output(spec: registry.ToolSpec, result: Any) -> BaseModel:
    if isinstance(result, spec.output_model):
        return result
    try:
        return spec.output_model.model_validate(result)
    except ValidationError as exc:
        raise ToolOutputError(spec.name, _terse(exc)) from exc


def _terse(exc: ValidationError) -> str:
    """A validation message short enough to log and specific enough to fix.

    Pydantic's full report embeds the offending INPUT, which for these tools is
    resume and JD text. Only the field path and the rule survive.
    """
    parts = []
    for error in exc.errors()[:4]:
        location = ".".join(str(piece) for piece in error.get("loc", ())) or "payload"
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or "failed validation"

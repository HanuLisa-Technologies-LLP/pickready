"""OpenTelemetry GenAI spans and metrics, emitted from the one router chokepoint.

WHERE IT ATTACHES, AND WHY THERE
--------------------------------
Every LLM call in this product already goes through
`llm_router.invoke_llm(task_type, ...)`, and this module hangs off that single
call site: one `with genai_span(...)` around the router's call. Four decorated
call sites would be four things to remember on the fifth agent; one chokepoint
cannot be forgotten.

THIS IS THE ONLY TRACER. The product ran a second, vendor-hosted tracer beside
it until 2026-09-24, when the owner ruled OpenTelemetry only. `agent_loop_span`
below is what replaced that tracer's loop-level parent run, so the loop still
has one span above the model calls it makes.

TWO HONEST CAVEATS ABOUT THE CONVENTIONS THEMSELVES
---------------------------------------------------
1. The OpenTelemetry GenAI semantic conventions moved to a dedicated
   repository (`open-telemetry/semantic-conventions-genai`) in June 2026, and
   NOTHING in them is marked Stable. The move is visible in the installed
   package: every constant in
   `opentelemetry.semconv._incubating.metrics.gen_ai_metrics` now carries a
   "Deprecated: Moved to the OpenTelemetry GenAI semantic conventions
   repository" note while keeping its name and value. That repository carries
   no tags, so there is no schema version to pin and this module deliberately
   does not claim one: an invented `schema_url` would assert a stability
   guarantee nobody has published.
2. Frameworks honour `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
   inconsistently. This module therefore reads no stability opt-in and branches
   on none. It emits ONE attribute set, always, and
   `tests/test_otel_span_shape.py` pins that set against a checked-in fixture
   by exporting a REAL span through the SDK. A convention drift becomes a
   failing test rather than a silently renamed field that a dashboard stops
   finding.

Every attribute name and every enum value below was read out of the installed
`opentelemetry-semantic-conventions` package rather than transcribed from
memory. This repository has been burned by a model id that was enshrined in
nine modules, pinned by tests, and never existed.

CONTENT NEVER CROSSES THIS MODULE, AND THE ENFORCEMENT IS AN ALLOWLIST
-----------------------------------------------------------------------
`observability/trace._SAFE_STAGE_KEYS` already applies this discipline to the
stage records that reach the database. Span and metric attributes get the same
treatment here, for a stronger reason: a trace store is far more widely
readable than the database, and the content in question is candidate answers,
resumes and job descriptions.

`_ALLOWED_SPAN_ATTRIBUTES` and `_ALLOWED_METRIC_ATTRIBUTES` are ALLOWLISTS,
never denylists. An attribute this module was not designed to carry is DROPPED,
so the next person adding "the prompt we sent" for debugging finds it absent
rather than finding it in a trace store a month later. A denylist would require
that person to have thought of the leak in order to prevent it, which is
exactly backwards.

Nothing here carries a score, a grade, a bucket name or an object key either.
The no-numbers rule is a rule about what reaches a client, and a score put on a
span is a score that reaches every dashboard the trace store feeds.

EXPORT IS OFF UNLESS AN ENDPOINT IS CONFIGURED, AND THAT IS A DECLARED STATE
-----------------------------------------------------------------------------
`OTEL_EXPORTER_OTLP_ENDPOINT` unset means disabled, which is what local
development and the test suite run with. `configure_from_environment` RETURNS
whether export was turned on and logs which state it settled in, so a disabled
pipeline is something an operator can read in a log line rather than something
they infer from an empty dashboard. It is not a swallowed error.

A BROKEN EXPORTER DEGRADES TO AN UNTRACED CALL, NEVER A FAILED ONE
-------------------------------------------------------------------
Observability is not worth an outage, and a traced call and an untraced call
must be indistinguishable to the caller. Every path that touches the SDK is
wrapped, and a failure leaves the pipeline disabled and logged.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

from opentelemetry.metrics import Histogram, MeterProvider
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes
from opentelemetry.semconv._incubating.metrics import gen_ai_metrics
from opentelemetry.semconv.attributes import error_attributes
from opentelemetry.trace import Span, StatusCode, Tracer, TracerProvider

logger = logging.getLogger(__name__)

__all__ = [
    "AgentLoopSpan",
    "ATTR_AGENT",
    "ATTR_PROMPT_DIGEST",
    "ATTR_PROMPT_VERSION",
    "ATTR_ROUTE",
    "ATTR_TASK_TYPE",
    "ATTR_TENANT_HASH",
    "GenAiSpan",
    "OPERATION_CHAT",
    "OPERATION_EMBEDDINGS",
    "OPERATION_EXECUTE_TOOL",
    "OPERATION_INVOKE_AGENT",
    "OPERATION_DURATION_BUCKETS",
    "TOKEN_USAGE_BUCKETS",
    "agent_loop_span",
    "configure_from_environment",
    "disable",
    "enable",
    "genai_span",
    "is_enabled",
    "record_usage_on_active_span",
    "tenant_hash",
]

#: The instrumentation scope. Named after this module so a backend can tell
#: Vivekium's own spans from any library instrumentation added later.
INSTRUMENTATION_NAME = "readypick.observability.otel"

# ── The four operations (values read from GenAiOperationNameValues) ──────────
#
# The conventions define more than four. These are the four this product
# performs: a completion, a tool call, an agent invocation, and an embedding.
OPERATION_CHAT = gen_ai_attributes.GenAiOperationNameValues.CHAT.value
OPERATION_EXECUTE_TOOL = gen_ai_attributes.GenAiOperationNameValues.EXECUTE_TOOL.value
OPERATION_INVOKE_AGENT = gen_ai_attributes.GenAiOperationNameValues.INVOKE_AGENT.value
OPERATION_EMBEDDINGS = gen_ai_attributes.GenAiOperationNameValues.EMBEDDINGS.value

_OPERATIONS = frozenset(
    {OPERATION_CHAT, OPERATION_EXECUTE_TOOL, OPERATION_INVOKE_AGENT, OPERATION_EMBEDDINGS}
)

# ── Attribute names ──────────────────────────────────────────────────────────
#
# The `gen_ai.*` names come from the semconv package, never typed out here: a
# renamed constant then breaks at import rather than emitting a field no
# dashboard queries.
ATTR_OPERATION_NAME = gen_ai_attributes.GEN_AI_OPERATION_NAME
ATTR_REQUEST_MODEL = gen_ai_attributes.GEN_AI_REQUEST_MODEL
ATTR_USAGE_INPUT_TOKENS = gen_ai_attributes.GEN_AI_USAGE_INPUT_TOKENS
ATTR_USAGE_OUTPUT_TOKENS = gen_ai_attributes.GEN_AI_USAGE_OUTPUT_TOKENS
ATTR_RESPONSE_FINISH_REASONS = gen_ai_attributes.GEN_AI_RESPONSE_FINISH_REASONS
ATTR_TOKEN_TYPE = gen_ai_attributes.GEN_AI_TOKEN_TYPE
ATTR_ERROR_TYPE = error_attributes.ERROR_TYPE

TOKEN_TYPE_INPUT = gen_ai_attributes.GenAiTokenTypeValues.INPUT.value
TOKEN_TYPE_OUTPUT = gen_ai_attributes.GenAiTokenTypeValues.COMPLETION.value

#: Vivekium's own dimensions. Prefixed rather than folded into `gen_ai.*`,
#: because a name in a published namespace that the published namespace does
#: not define is the kind of thing a future collector rejects or a future
#: convention collides with.
ATTR_TASK_TYPE = "readypick.task_type"
#: The vendor's reported prompt-cache hit, a SUBSET of the input tokens.
#:
#: Prefixed rather than folded into `gen_ai.*` for the reason stated above: the
#: semconv package pinned here defines no cached-token attribute, and emitting
#: a `gen_ai.`-prefixed name it does not define is exactly the thing a future
#: collector rejects. It is SPAN-ONLY and deliberately absent from the metric
#: allowlist below: it is a token count rather than a dimension, so putting it
#: on a histogram's attributes would multiply the cardinality by an unbounded
#: number for no query anybody would write.
ATTR_CACHED_INPUT_TOKENS = "readypick.cached_input_tokens"
ATTR_PROMPT_VERSION = "readypick.prompt_version"
ATTR_PROMPT_DIGEST = "readypick.prompt_digest"
ATTR_TENANT_HASH = "readypick.tenant_hash"
ATTR_AGENT = "readypick.agent"
ATTR_ROUTE = "readypick.route"

#: THE ALLOWLIST. Everything a span may carry, and nothing else. An attribute
#: outside this set is dropped by `_filter`, silently to the caller and loudly
#: to the test suite: `tests/test_otel_no_content.py` feeds a payload
#: containing candidate text, a prompt body, a score, a bucket name and an
#: object key, and asserts none of them survive.
#:
#: `gen_ai.operation.name` is here and is not in the specification's own
#: Vivekium list because the conventions require it to identify the operation;
#: without it a span says which model was called and not what was asked of it.
#: `error.type` is here because the operation-duration metric is uninterpretable
#: when a failed call is indistinguishable from a successful one, and an
#: exception CLASS NAME is precisely what `RequestTrace` already records.
_ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        ATTR_OPERATION_NAME,
        ATTR_REQUEST_MODEL,
        ATTR_USAGE_INPUT_TOKENS,
        ATTR_USAGE_OUTPUT_TOKENS,
        ATTR_RESPONSE_FINISH_REASONS,
        ATTR_ERROR_TYPE,
        ATTR_TASK_TYPE,
        ATTR_CACHED_INPUT_TOKENS,
        ATTR_PROMPT_VERSION,
        ATTR_PROMPT_DIGEST,
        ATTR_TENANT_HASH,
        ATTR_AGENT,
        ATTR_ROUTE,
    }
)

#: A SECOND allowlist, deliberately narrower, and not a second code path: it
#: governs a different signal with a different cost model. Every distinct
#: attribute combination on a metric is a time series that exists for ever, so
#: `readypick.tenant_hash` and `readypick.prompt_digest` are span-only. Both are
#: unbounded: one grows with the customer list, the other with every prompt
#: edit, and either would multiply the cardinality of both histograms by a
#: number nobody controls. Everything left here is a bounded enum.
_ALLOWED_METRIC_ATTRIBUTES = frozenset(
    {
        ATTR_OPERATION_NAME,
        ATTR_REQUEST_MODEL,
        ATTR_TOKEN_TYPE,
        ATTR_ERROR_TYPE,
        ATTR_TASK_TYPE,
        ATTR_AGENT,
        ATTR_ROUTE,
    }
)

#: The agent loop's own span. INTERNAL, not a GenAI operation: a loop is the
#: bounded generate/evaluate/revise cycle ABOVE the model calls it makes, and
#: each of those calls already has its GenAI span as a child. So these names are
#: Vivekium-prefixed and live under a THIRD allowlist rather than widening the
#: GenAI one, whose exact membership `tests/test_otel_no_content.py` pins.
ATTR_LOOP_NAME = "readypick.loop.name"
ATTR_LOOP_MAX_ATTEMPTS = "readypick.loop.max_attempts"
ATTR_LOOP_DEADLINE_SECONDS = "readypick.loop.deadline_seconds"
ATTR_LOOP_MAX_GENERATED_TOKENS = "readypick.loop.max_generated_tokens"
ATTR_LOOP_ATTEMPTS = "readypick.loop.attempts"
ATTR_LOOP_DEGRADED = "readypick.loop.degraded"
ATTR_LOOP_ELAPSED_MS = "readypick.loop.elapsed_ms"
ATTR_LOOP_GENERATED_TOKENS = "readypick.loop.generated_tokens"
#: Defect TYPES only, never a defect's `detail`. A detail can quote the output
#: it rejected, and the output was written from a candidate's answers; the
#: same rule `observability/trace._SAFE_STAGE_KEYS` applies to stored traces.
ATTR_LOOP_DEFECT_TYPES = "readypick.loop.defect_types"

#: Everything a loop span may carry. Counts, bounds, a boolean, a loop name the
#: code chose and a list of defect type names the code chose: nothing a model
#: or a candidate wrote.
_ALLOWED_LOOP_SPAN_ATTRIBUTES = frozenset(
    {
        ATTR_LOOP_NAME,
        ATTR_LOOP_MAX_ATTEMPTS,
        ATTR_LOOP_DEADLINE_SECONDS,
        ATTR_LOOP_MAX_GENERATED_TOKENS,
        ATTR_LOOP_ATTEMPTS,
        ATTR_LOOP_DEGRADED,
        ATTR_LOOP_ELAPSED_MS,
        ATTR_LOOP_GENERATED_TOKENS,
        ATTR_LOOP_DEFECT_TYPES,
        ATTR_ERROR_TYPE,
    }
)

# ── The published bucket boundaries, verbatim ────────────────────────────────
#
# Copied from the conventions' metric definitions rather than chosen. A
# histogram whose buckets differ from the published ones cannot be compared
# with anybody else's, which is most of what adopting a convention buys. They
# are passed as `explicit_bucket_boundaries_advisory`, which is the API the
# installed `opentelemetry-api` offers for exactly this: the instrument carries
# its own advice, so a backend gets the right buckets without every deployment
# having to register a View.
#: `gen_ai.client.token.usage`, unit `{token}`.
TOKEN_USAGE_BUCKETS: tuple[float, ...] = (
    1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144,
)
#: `gen_ai.client.operation.duration`, unit `s`.
OPERATION_DURATION_BUCKETS: tuple[float, ...] = (
    0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28,
    2.56, 5.12, 10.24, 20.48, 40.96, 81.92,
)

#: How much of the tenant hash is kept. Long enough that two customers
#: colliding is not a practical concern, short enough that it reads as an
#: opaque label rather than as a value somebody might try to reverse.
_TENANT_HASH_CHARS = 16


def tenant_hash(tenant_id: uuid.UUID | str | None) -> str | None:
    """A stable, opaque label for one tenant. None passes through as None.

    A tenant id joins a trace store row straight back to a customer identity,
    and a trace store is read by more people than the database is. A hash keeps
    the question a dashboard actually asks -- is one customer generating all of
    this -- while answering nothing about who they are.
    """
    if tenant_id is None:
        return None
    return hashlib.sha256(str(tenant_id).encode("utf-8")).hexdigest()[:_TENANT_HASH_CHARS]


@dataclass(frozen=True)
class _Pipeline:
    """The installed emitters. Present means enabled; absent means disabled."""

    tracer: Tracer
    token_usage: Histogram
    operation_duration: Histogram


_pipeline: _Pipeline | None = None
#: Whether `configure_from_environment` has run. Separate from `_pipeline`,
#: because "configured and deliberately off" and "never configured" are
#: different states and only the second one should trigger a lazy configure.
_configured: bool = False


def enable(tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
    """Install the providers this module emits through.

    Explicit rather than global: the test suite installs an in-memory pair and
    reads real exported spans back, and a deployment installs OTLP ones through
    `configure_from_environment`. Both arrive at the same single `_Pipeline`,
    so there is one emit path and two ways of populating it.
    """
    global _pipeline, _configured
    meter = meter_provider.get_meter(INSTRUMENTATION_NAME)
    _pipeline = _Pipeline(
        tracer=tracer_provider.get_tracer(INSTRUMENTATION_NAME),
        token_usage=meter.create_histogram(
            name=gen_ai_metrics.GEN_AI_CLIENT_TOKEN_USAGE,
            unit="{token}",
            description="Number of input and output tokens used.",
            explicit_bucket_boundaries_advisory=TOKEN_USAGE_BUCKETS,
        ),
        operation_duration=meter.create_histogram(
            name=gen_ai_metrics.GEN_AI_CLIENT_OPERATION_DURATION,
            unit="s",
            description="GenAI operation duration.",
            explicit_bucket_boundaries_advisory=OPERATION_DURATION_BUCKETS,
        ),
    )
    _configured = True


def disable() -> None:
    """Turn emission off and mark the module configured.

    Marked configured on purpose: an operator who turned it off should not have
    the next span quietly reconfigure it from the environment.
    """
    global _pipeline, _configured
    _pipeline = None
    _configured = True


def is_enabled() -> bool:
    """Whether a span emitted right now would be recorded."""
    return _pipeline is not None


def configure_from_environment() -> bool:
    """Build the OTLP pipeline if an endpoint is configured. Returns the state.

    Returns True when export is on and False when it is off, and logs which,
    because a disabled exporter and a broken one produce the same empty
    dashboard and must not produce the same empty log.

    `OTEL_EXPORTER_OTLP_ENDPOINT` unset is the ordinary state for local
    development and for the test suite, and it is a DECISION, not a failure:
    nothing is constructed and no network call is made.
    """
    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not endpoint:
        logger.info("otel.export_disabled reason=no_endpoint")
        disable()
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        sdk_tracer_provider = SdkTracerProvider()
        sdk_tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        sdk_meter_provider = SdkMeterProvider(
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
        )
        enable(sdk_tracer_provider, sdk_meter_provider)
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        # A broken exporter degrades to untraced calls. It is logged at
        # WARNING and named, because this is the one state that looks identical
        # to "correctly switched off" from the outside.
        logger.warning("otel.export_unavailable error=%s", type(exc).__name__)
        disable()
        return False

    logger.info("otel.export_enabled transport=otlp_http")
    return True


def _pipeline_or_none() -> _Pipeline | None:
    """The installed pipeline, configuring from the environment exactly once."""
    global _configured
    if not _configured:
        configure_from_environment()
    return _pipeline


def _filter(
    attributes: Mapping[str, Any], allowed: frozenset[str]
) -> dict[str, Any]:
    """Keep the allowed keys with a value, drop everything else.

    A None value is dropped rather than emitted: an attribute present and empty
    reads on a dashboard as "the model returned nothing", which is a different
    claim from "this call did not report it".
    """
    return {
        key: value
        for key, value in attributes.items()
        if key in allowed and value is not None
    }


class GenAiSpan:
    """The handle a caller records usage and outcome on.

    Every write is failure-tolerant for the reason in the module docstring: a
    caller must not be able to tell a traced call from an untraced one, and it
    must certainly not fail because of one.
    """

    __slots__ = ("_span", "_pipeline", "_metric_attributes", "_started")

    def __init__(
        self,
        span: Span,
        pipeline: _Pipeline,
        metric_attributes: dict[str, Any],
        started: float,
    ) -> None:
        self._span = span
        self._pipeline = pipeline
        self._metric_attributes = metric_attributes
        self._started = started

    def record_usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        """Put the token counts on the span and into the usage histogram.

        Recorded as two points on ONE histogram distinguished by
        `gen_ai.token.type`, which is what the convention specifies. Two
        separate instruments would be a second name for one measurement.

        `cached_input_tokens` is the vendor's reported prompt-cache hit and is
        a SUBSET of `input_tokens`, so it is never added to the histogram: the
        same tokens would then be counted twice and every input-token total in
        every dashboard would silently inflate. It is a span attribute only,
        and None (the response said nothing about caching) leaves the attribute
        off the span entirely rather than writing a zero somebody would read as
        a measured miss.
        """
        attributes = _filter(
            {
                ATTR_USAGE_INPUT_TOKENS: input_tokens,
                ATTR_USAGE_OUTPUT_TOKENS: output_tokens,
                ATTR_CACHED_INPUT_TOKENS: cached_input_tokens,
            },
            _ALLOWED_SPAN_ATTRIBUTES,
        )
        try:
            self._span.set_attributes(attributes)
            if input_tokens is not None:
                self._pipeline.token_usage.record(
                    input_tokens,
                    attributes={**self._metric_attributes, ATTR_TOKEN_TYPE: TOKEN_TYPE_INPUT},
                )
            if output_tokens is not None:
                self._pipeline.token_usage.record(
                    output_tokens,
                    attributes={**self._metric_attributes, ATTR_TOKEN_TYPE: TOKEN_TYPE_OUTPUT},
                )
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.record_usage_failed")

    def record_finish_reasons(self, reasons: Sequence[str]) -> None:
        """Record why generation stopped: `stop`, `length`, `content_filter`.

        A vendor's own vocabulary, which is a short bounded set of protocol
        words and carries no candidate content. It is the field that separates
        "the model answered" from "the model was cut off mid sentence", and
        this product has a standing rule about what a model does with half a
        sentence.
        """
        try:
            self._span.set_attributes(
                _filter(
                    {ATTR_RESPONSE_FINISH_REASONS: tuple(str(r) for r in reasons)},
                    _ALLOWED_SPAN_ATTRIBUTES,
                )
            )
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.record_finish_reasons_failed")

    def record_error(self, exc: BaseException) -> None:
        """Mark the span failed, by exception CLASS NAME only.

        Never `str(exc)`. A router error message quotes the task type and the
        model, which is safe, but a message is prose somebody may later widen
        to include a row, an answer or a key. The class name cannot be widened.
        """
        error_type = type(exc).__name__
        self._metric_attributes[ATTR_ERROR_TYPE] = error_type
        try:
            self._span.set_attributes(
                _filter({ATTR_ERROR_TYPE: error_type}, _ALLOWED_SPAN_ATTRIBUTES)
            )
            self._span.set_status(StatusCode.ERROR)
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.record_error_failed")

    def _record_duration(self) -> None:
        """Close the operation-duration histogram point. Called by the manager."""
        try:
            self._pipeline.operation_duration.record(
                time.monotonic() - self._started,
                attributes=_filter(self._metric_attributes, _ALLOWED_METRIC_ATTRIBUTES),
            )
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.record_duration_failed")


#: The span currently open on this task, so a deeper frame can report token
#: counts without the handle being threaded through every function between.
#: This exists because of exactly one real shape: `llm_router.invoke_llm` is
#: the chokepoint and knows the task type and the model, while the token counts
#: come back inside the retry graph's attempt node and `_invoke_llm_inner`
#: returns a bare string, so the counts are simply not visible at the
#: chokepoint. A contextvar rather than a module global: two assessments score
#: concurrently in one worker, and a global would file one candidate's tokens
#: against another's span.
_active_span: contextvars.ContextVar["GenAiSpan | None"] = contextvars.ContextVar(
    "readypick_otel_active_span", default=None
)


def record_usage_on_active_span(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_input_tokens: int | None = None,
) -> bool:
    """Report token counts against whatever span is open on this task.

    Returns whether a span was there to receive them, so a caller that expected
    one and got none can say so rather than silently recording nothing. It is
    NOT a second way of recording usage: it finds the handle and calls
    `GenAiSpan.record_usage`, which stays the only writer.
    """
    span = _active_span.get()
    if span is None:
        return False
    span.record_usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    return True


@contextlib.contextmanager
def genai_span(
    operation: str,
    *,
    request_model: str | None = None,
    task_type: str | None = None,
    prompt_version: str | None = None,
    prompt_digest: str | None = None,
    tenant_id: uuid.UUID | str | None = None,
    agent: str | None = None,
    route: str | None = None,
) -> Iterator[GenAiSpan | None]:
    """Trace one GenAI operation. Yields a handle, or None when disabled.

    THE ENTRY POINT. This is what `llm_router.invoke_llm` wraps its call in,
    and it is the only public way to start a GenAI span here.

    `operation` is one of `OPERATION_CHAT`, `OPERATION_EXECUTE_TOOL`,
    `OPERATION_INVOKE_AGENT` or `OPERATION_EMBEDDINGS`. An unrecognised value is
    refused, because the operation name is the dimension every GenAI dashboard
    groups by and a typo there produces a span that is present, plausible and
    invisible to every query.

    Everything else is optional and an absent value is simply not emitted. That
    matters at the router: `invoke_llm` knows the task type and the model, and
    does not know which prompt file the caller rendered, so `prompt_version` and
    `prompt_digest` arrive only from a caller that has them.

    The span name follows the conventions' own rule, `{operation} {model}`, so
    it reads as "chat gpt-5.6-terra" in a backend that groups by span name.

    Yields None when export is off, so a caller writes `if handle is not None`
    and never has to ask whether tracing is configured.
    """
    if operation not in _OPERATIONS:
        raise ValueError(
            f"Unknown GenAI operation {operation!r}; "
            f"expected one of {sorted(_OPERATIONS)}"
        )

    pipeline = _pipeline_or_none()
    if pipeline is None:
        yield None
        return

    attributes = _filter(
        {
            ATTR_OPERATION_NAME: operation,
            ATTR_REQUEST_MODEL: request_model,
            ATTR_TASK_TYPE: task_type,
            ATTR_PROMPT_VERSION: prompt_version,
            ATTR_PROMPT_DIGEST: prompt_digest,
            ATTR_TENANT_HASH: tenant_hash(tenant_id),
            ATTR_AGENT: agent,
            ATTR_ROUTE: route,
        },
        _ALLOWED_SPAN_ATTRIBUTES,
    )
    span_name = f"{operation} {request_model}" if request_model else operation

    try:
        manager = pipeline.tracer.start_as_current_span(span_name, attributes=attributes)
        span = manager.__enter__()
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        logger.info("otel.span_unavailable error=%s", type(exc).__name__)
        yield None
        return

    handle = GenAiSpan(
        span=span,
        pipeline=pipeline,
        metric_attributes=_filter(attributes, _ALLOWED_METRIC_ATTRIBUTES),
        started=time.monotonic(),
    )
    token = _active_span.set(handle)
    try:
        yield handle
    except BaseException as exc:
        # The caller raised. Record it and re-raise unchanged: this module
        # observes an outcome, it never changes one.
        handle.record_error(exc)
        raise
    finally:
        _active_span.reset(token)
        handle._record_duration()
        try:
            manager.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.span_exit_failed")


class AgentLoopSpan:
    """The handle `agent_loop.run_loop` records its outcome on.

    Failure tolerant for the reason every writer in this module is: a loop must
    not be able to tell a traced run from an untraced one.
    """

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def end(
        self,
        *,
        attempts: int,
        degraded: bool,
        elapsed_ms: int,
        generated_tokens: int,
        defect_types: Sequence[str],
        error_type: str | None,
    ) -> None:
        """Record what the loop did. Counts, a flag, type names; never content.

        `error_type` is an exception CLASS NAME, the only error text this
        module ever carries, and a degraded run marks the span failed so a
        dashboard counting failed loops does not need to know the attribute.
        """
        attributes = _filter(
            {
                ATTR_LOOP_ATTEMPTS: attempts,
                ATTR_LOOP_DEGRADED: degraded,
                ATTR_LOOP_ELAPSED_MS: elapsed_ms,
                ATTR_LOOP_GENERATED_TOKENS: generated_tokens,
                ATTR_LOOP_DEFECT_TYPES: tuple(sorted({str(t) for t in defect_types})),
                ATTR_ERROR_TYPE: error_type,
            },
            _ALLOWED_LOOP_SPAN_ATTRIBUTES,
        )
        try:
            self._span.set_attributes(attributes)
            if degraded:
                self._span.set_status(StatusCode.ERROR)
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.loop_end_failed")


@contextlib.contextmanager
def agent_loop_span(
    name: str,
    *,
    max_attempts: int,
    deadline_seconds: float,
    max_generated_tokens: int,
) -> Iterator[AgentLoopSpan | None]:
    """Trace one bounded agent loop as an INTERNAL span. None when disabled.

    The model calls the loop makes open their own GenAI spans inside this one,
    so a trace reads as one loop and its attempts. `name` is the loop name the
    CODE chose (`run_loop(name=...)`), never anything a model or a candidate
    wrote, and the span name is `agent_loop {name}` so a backend grouping by
    span name separates the loops with no per-loop wiring.

    An exception from the body is recorded by class name and re-raised
    unchanged; `run_loop` never raises, so in practice what is recorded is the
    outcome the loop reports through `AgentLoopSpan.end`.
    """
    pipeline = _pipeline_or_none()
    if pipeline is None:
        yield None
        return

    attributes = _filter(
        {
            ATTR_LOOP_NAME: name,
            ATTR_LOOP_MAX_ATTEMPTS: max_attempts,
            ATTR_LOOP_DEADLINE_SECONDS: deadline_seconds,
            ATTR_LOOP_MAX_GENERATED_TOKENS: max_generated_tokens,
        },
        _ALLOWED_LOOP_SPAN_ATTRIBUTES,
    )
    try:
        manager = pipeline.tracer.start_as_current_span(
            f"agent_loop {name}", attributes=attributes
        )
        span = manager.__enter__()
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        logger.info("otel.loop_span_unavailable error=%s", type(exc).__name__)
        yield None
        return

    try:
        yield AgentLoopSpan(span)
    except BaseException as exc:
        # Observe, never change: record the class name and re-raise unchanged.
        try:
            span.set_attributes(
                _filter(
                    {ATTR_ERROR_TYPE: type(exc).__name__},
                    _ALLOWED_LOOP_SPAN_ATTRIBUTES,
                )
            )
            span.set_status(StatusCode.ERROR)
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.loop_record_error_failed")
        raise
    finally:
        try:
            manager.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 -- observability never fails a call
            logger.debug("otel.loop_span_exit_failed")

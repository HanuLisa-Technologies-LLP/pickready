"""No span attribute carries candidate text, a prompt body, a score, a bucket
name or an object key.

WHY THE PAYLOAD CONTAINS ALL FIVE
----------------------------------
A test that traces a clean call and then asserts no content appears proves
nothing: there was never any content to leak. So the payload below is the
realistic one -- the dict somebody debugging a bad report would reach for, with
the candidate's answer, the rendered prompt, the internal score, the media
bucket and the object key sitting beside the legitimate identifiers -- and the
assertion is that they were DROPPED. That is a statement about the allowlist,
not about the sample.

An ALLOWLIST, never a denylist. `observability/trace._SAFE_STAGE_KEYS` already
applies this discipline to what reaches the database; a trace store is read by
more people than the database is, and the content in question is candidate
answers, resumes and job descriptions. A denylist would require the next person
to have thought of the leak in order to prevent it, which is backwards: they
should find "the prompt we sent" absent from the trace store rather than find
it there a month later.

A score is on that list for the product's own reason. Scores are internal, the
conversion to one of four words happens server-side at the serializer, and a
score written onto a span reaches every dashboard the trace store feeds.
"""
from __future__ import annotations

import inspect
import json

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.services.observability import otel

# ── The five things that must never be traced ────────────────────────────────
CANDIDATE_TEXT = (
    "I led the Kafka partition rebalance migration at Acme Retail across four "
    "regions and cut consumer lag from minutes to seconds."
)
PROMPT_BODY = (
    "You are Siddhi. Compose the PRISM report from the cited evidence only. "
    "Every statement must carry a citation."
)
SCORE = 87.5
BUCKET_NAME = "readypick-pilot-assessment-media"
OBJECT_KEY = "project-intake/1f2c9a/candidate-8821/portfolio-final.zip"

FORBIDDEN_VALUES = (CANDIDATE_TEXT, PROMPT_BODY, str(SCORE), BUCKET_NAME, OBJECT_KEY)

#: The realistic payload. Legitimate identifiers and the five forbidden values
#: in one dict, exactly as a debugging session would assemble them.
REALISTIC_PAYLOAD = {
    otel.ATTR_OPERATION_NAME: otel.OPERATION_CHAT,
    otel.ATTR_REQUEST_MODEL: "gpt-5.6-terra",
    otel.ATTR_USAGE_INPUT_TOKENS: 1200,
    otel.ATTR_USAGE_OUTPUT_TOKENS: 340,
    otel.ATTR_TASK_TYPE: "report_synthesis",
    otel.ATTR_TENANT_HASH: "bafde89c041e1756",
    "gen_ai.prompt": PROMPT_BODY,
    "gen_ai.completion": CANDIDATE_TEXT,
    "readypick.answer_text": CANDIDATE_TEXT,
    "readypick.match_score": SCORE,
    "readypick.storage_bucket": BUCKET_NAME,
    "readypick.object_key": OBJECT_KEY,
}

FORBIDDEN_KEYS = (
    "gen_ai.prompt",
    "gen_ai.completion",
    "readypick.answer_text",
    "readypick.match_score",
    "readypick.storage_bucket",
    "readypick.object_key",
)


@pytest.fixture
def exporter():
    """In-memory providers, and the module put back exactly as it was found."""
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    otel.enable(tracer_provider, MeterProvider(metric_readers=[InMemoryMetricReader()]))
    try:
        yield span_exporter
    finally:
        otel._pipeline = None
        otel._configured = False


def test_the_allowlist_drops_all_five_at_the_chokepoint():
    """`_filter` is the one gate every span attribute passes through."""
    kept = otel._filter(REALISTIC_PAYLOAD, otel._ALLOWED_SPAN_ATTRIBUTES)

    for key in FORBIDDEN_KEYS:
        assert key not in kept, f"{key} survived the allowlist"
    # And the legitimate half is genuinely still there, so this is not passing
    # because everything was dropped.
    assert kept[otel.ATTR_REQUEST_MODEL] == "gpt-5.6-terra"
    assert kept[otel.ATTR_USAGE_INPUT_TOKENS] == 1200
    assert kept[otel.ATTR_TASK_TYPE] == "report_synthesis"


def test_a_real_exported_span_built_from_that_payload_carries_none_of_it(exporter):
    """End to end: emit, export, then sweep the serialised span for each value."""
    with otel.genai_span(
        REALISTIC_PAYLOAD[otel.ATTR_OPERATION_NAME],
        request_model=REALISTIC_PAYLOAD[otel.ATTR_REQUEST_MODEL],
        task_type=REALISTIC_PAYLOAD[otel.ATTR_TASK_TYPE],
        prompt_version="3",
        prompt_digest="7f2a1c9d",
        tenant_id="11111111-1111-1111-1111-111111111111",
        agent="siddhi",
        route="ecs",
    ) as span:
        span.record_usage(
            input_tokens=REALISTIC_PAYLOAD[otel.ATTR_USAGE_INPUT_TOKENS],
            output_tokens=REALISTIC_PAYLOAD[otel.ATTR_USAGE_OUTPUT_TOKENS],
        )
        span.record_finish_reasons(["stop"])

    exported = exporter.get_finished_spans()
    assert len(exported) == 1
    blob = json.dumps(
        {
            "name": exported[0].name,
            "attributes": {k: str(v) for k, v in exported[0].attributes.items()},
        }
    )
    for value in FORBIDDEN_VALUES:
        assert value not in blob, f"a forbidden value reached the span: {value[:40]}"

    assert set(exported[0].attributes) <= otel._ALLOWED_SPAN_ATTRIBUTES


def test_an_error_is_traced_by_class_name_and_never_by_its_message(exporter):
    """A message is prose somebody may later widen to quote a row.

    The router's own error strings name the task type and the model today. The
    rule is not that today's message is unsafe, it is that a class name cannot
    become unsafe later.
    """
    class LLMUnavailableError(Exception):
        pass

    with pytest.raises(LLMUnavailableError):
        with otel.genai_span(otel.OPERATION_CHAT, request_model="gpt-5.6-terra"):
            raise LLMUnavailableError(f"scoring failed for answer: {CANDIDATE_TEXT}")

    span = exporter.get_finished_spans()[0]
    assert span.attributes[otel.ATTR_ERROR_TYPE] == "LLMUnavailableError"
    for value in FORBIDDEN_VALUES:
        assert value not in json.dumps({k: str(v) for k, v in span.attributes.items()})


def test_the_entry_point_accepts_no_free_form_payload():
    """The structural half of the guarantee.

    The allowlist stops a forbidden key. This stops the shape that would make
    the allowlist bypassable: a `**kwargs`, a `metadata` dict or a `messages`
    argument on the entry point. Every parameter is a named scalar, so smuggling
    a prompt body in requires editing this module and this test.
    """
    signature = inspect.signature(otel.genai_span)
    for name, parameter in signature.parameters.items():
        assert parameter.kind is not inspect.Parameter.VAR_KEYWORD, (
            f"{name} is a free-form payload on the tracing entry point"
        )
        assert parameter.kind is not inspect.Parameter.VAR_POSITIONAL, (
            f"{name} is a free-form payload on the tracing entry point"
        )
    forbidden_names = {"messages", "metadata", "inputs", "outputs", "prompt", "payload"}
    assert not forbidden_names & set(signature.parameters)


def test_the_span_allowlist_admits_no_content_bearing_name():
    """The conventions themselves define content fields. None of them are here.

    `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`,
    `gen_ai.output.messages`, `gen_ai.system_instructions`,
    `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` and
    `gen_ai.retrieval.query.text` all exist in the installed semconv package and
    all carry text a candidate wrote or a prompt asked. Adopting a convention
    does not mean adopting every field in it.
    """
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes

    content_bearing = {
        gen_ai_attributes.GEN_AI_PROMPT,
        gen_ai_attributes.GEN_AI_COMPLETION,
        gen_ai_attributes.GEN_AI_INPUT_MESSAGES,
        gen_ai_attributes.GEN_AI_OUTPUT_MESSAGES,
        gen_ai_attributes.GEN_AI_SYSTEM_INSTRUCTIONS,
        gen_ai_attributes.GEN_AI_TOOL_CALL_ARGUMENTS,
        gen_ai_attributes.GEN_AI_TOOL_CALL_RESULT,
        gen_ai_attributes.GEN_AI_RETRIEVAL_QUERY_TEXT,
        gen_ai_attributes.GEN_AI_EVALUATION_SCORE_VALUE,
        gen_ai_attributes.GEN_AI_EVALUATION_EXPLANATION,
    }
    assert not content_bearing & otel._ALLOWED_SPAN_ATTRIBUTES
    assert not content_bearing & otel._ALLOWED_METRIC_ATTRIBUTES


def test_a_tenant_id_is_hashed_and_never_traced_raw():
    """A raw tenant id joins a trace store row back to a customer identity."""
    tenant_id = "11111111-1111-1111-1111-111111111111"
    hashed = otel.tenant_hash(tenant_id)
    assert hashed is not None
    assert tenant_id not in hashed
    assert otel.tenant_hash(None) is None
    assert otel.tenant_hash(tenant_id) == hashed

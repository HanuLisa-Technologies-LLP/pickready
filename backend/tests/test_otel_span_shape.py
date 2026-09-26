"""A REAL exported span, compared against a checked-in fixture.

WHY THIS TEST EXISTS AT ALL
---------------------------
The OpenTelemetry GenAI semantic conventions moved to a dedicated repository in
June 2026 and nothing in them is marked Stable. The repository carries no tags,
so there is no schema version to pin, and frameworks honour
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` inconsistently. That
combination means the realistic failure is not a crash: it is
`gen_ai.usage.input_tokens` being renamed in a dependency upgrade, the module
happily emitting the new name, and every dashboard querying the old one going
quiet with nothing raising anywhere.

So this test does not assert against the specification from memory. It runs the
module through the SDK's `InMemorySpanExporter`, reads the span the exporter
actually received, and compares it with `tests/fixtures/otel/genai_spans.json`.
A drift becomes a failing test with a diff naming the old and the new field.

The INPUT lives here as code and the EXPECTATION lives in the fixture as data,
so a change to what the module emits shows up as a data diff a reviewer can
read rather than as an edit buried in an assertion.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.services.observability import otel

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "otel" / "genai_spans.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
TENANT_ID = FIXTURE["tenant_id"]


class _Failure(Exception):
    """Stands in for the router's own error, named so `error.type` is stable.

    The fixture asserts the CLASS NAME reaches the span, which is why this is a
    class with a deliberate name rather than a bare `RuntimeError`.
    """


LLMUnavailableError = type("LLMUnavailableError", (_Failure,), {})


@pytest.fixture
def pipeline():
    """Install in-memory providers, and put the module back afterwards.

    Restores both module globals rather than calling `disable()`: `disable()`
    leaves the module marked configured, which would stop the next test in the
    session from configuring itself from the environment and make this fixture
    change behaviour outside its own test.
    """
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    otel.enable(tracer_provider, MeterProvider(metric_readers=[reader]))
    try:
        yield exporter, reader
    finally:
        otel._pipeline = None
        otel._configured = False


def _emit_all() -> None:
    """Produce one span per fixture case. The input, as code."""
    with otel.genai_span(
        otel.OPERATION_CHAT,
        request_model="gpt-5.6-terra",
        task_type="report_synthesis",
        prompt_version="3",
        prompt_digest="7f2a1c9d",
        tenant_id=TENANT_ID,
        agent="siddhi",
        route="ecs",
    ) as span:
        span.record_usage(input_tokens=1200, output_tokens=340)
        span.record_finish_reasons(["stop"])

    with otel.genai_span(
        otel.OPERATION_EMBEDDINGS,
        request_model="voyage-4",
        task_type="extraction",
        tenant_id=TENANT_ID,
        agent="yukti",
        route="lambda",
    ) as span:
        span.record_usage(input_tokens=512)

    with otel.genai_span(
        otel.OPERATION_EXECUTE_TOOL,
        task_type="job_facts",
        tenant_id=TENANT_ID,
        agent="email_agent",
        route="lambda",
    ):
        pass

    with otel.genai_span(
        otel.OPERATION_INVOKE_AGENT,
        request_model="gpt-5.6-luna",
        task_type="extraction",
        prompt_version="1",
        prompt_digest="c41d0e2b",
        tenant_id=TENANT_ID,
        agent="bodha",
        route="ecs",
    ) as span:
        span.record_usage(input_tokens=8100, output_tokens=2048)
        span.record_finish_reasons(["length"])

    with pytest.raises(LLMUnavailableError):
        with otel.genai_span(
            otel.OPERATION_CHAT,
            request_model="gpt-5.6-terra",
            task_type="behavioral_assessment",
            tenant_id=TENANT_ID,
            agent="miti",
            route="ecs",
        ):
            raise LLMUnavailableError("the vendor exhausted its retry budget")


def _exported(exporter: InMemorySpanExporter) -> list:
    spans = list(exporter.get_finished_spans())
    assert len(spans) == len(FIXTURE["spans"]), (
        f"expected one span per fixture case, got {len(spans)}"
    )
    return spans


def test_every_exported_span_matches_the_fixture(pipeline):
    """Name and full attribute map, case by case, against the checked-in data."""
    exporter, _ = pipeline
    _emit_all()

    for span, expected in zip(_exported(exporter), FIXTURE["spans"]):
        case = expected["case"]
        assert span.name == expected["span_name"], f"span name drifted for {case}"
        actual = {key: _plain(value) for key, value in span.attributes.items()}
        assert actual == expected["attributes"], f"attributes drifted for {case}"


def test_a_failed_call_carries_the_error_type_and_an_error_status(pipeline):
    """A duration histogram cannot be read if failures look like successes."""
    exporter, _ = pipeline
    _emit_all()

    span = _exported(exporter)[-1]
    expected = FIXTURE["spans"][-1]
    assert span.attributes[otel.ATTR_ERROR_TYPE] == expected["attributes"]["error.type"]
    assert span.status.status_code.name == expected["status_code"]


def test_both_histograms_carry_the_published_bucket_boundaries(pipeline):
    """The buckets are the conventions' own, verbatim.

    A histogram whose boundaries differ from the published ones cannot be
    compared with anybody else's, which is most of what adopting a convention
    buys. They travel as `explicit_bucket_boundaries_advisory` on the
    instrument, so this asserts they survive all the way to the exported data
    point rather than only reaching the constructor.
    """
    _, reader = pipeline
    _emit_all()

    points = _metric_points(reader)
    for name, expected in FIXTURE["metrics"].items():
        assert name in points, f"{name} was never recorded"
        unit, data_points = points[name]
        assert unit == expected["unit"]
        for data_point in data_points:
            assert list(data_point.explicit_bounds) == expected["explicit_bounds"]


def test_metric_attribute_keys_stay_bounded(pipeline):
    """No tenant hash and no prompt digest on a metric.

    Every distinct attribute combination is a time series that exists for ever.
    Both of those are unbounded, one growing with the customer list and one with
    every prompt edit, so both are span-only by the second allowlist.
    """
    _, reader = pipeline
    _emit_all()

    points = _metric_points(reader)
    for name, expected in FIXTURE["metrics"].items():
        _, data_points = points[name]
        seen: set[str] = set()
        for data_point in data_points:
            seen.update(data_point.attributes.keys())
        assert sorted(seen) == expected["attribute_keys"], f"metric dimensions drifted for {name}"


def test_usage_reported_from_a_deeper_frame_lands_on_the_open_span(pipeline):
    """The shape the router actually has.

    `invoke_llm` knows the task type and the model; the token counts come back
    inside the retry graph's attempt node, and the function between them returns
    a bare string. So the counts are reported against whatever span is open on
    this task rather than through a handle threaded down four frames.
    """
    exporter, _ = pipeline

    async def attempt() -> None:
        assert otel.record_usage_on_active_span(input_tokens=90, output_tokens=12) is True

    with otel.genai_span(otel.OPERATION_CHAT, request_model="gpt-5.6-terra"):
        asyncio.run(attempt())

    span = exporter.get_finished_spans()[0]
    assert span.attributes[otel.ATTR_USAGE_INPUT_TOKENS] == 90
    assert span.attributes[otel.ATTR_USAGE_OUTPUT_TOKENS] == 12


def test_usage_with_no_open_span_says_so_rather_than_vanishing():
    """False, not None. A caller that expected a span can report its absence."""
    otel._pipeline = None
    otel._configured = True
    try:
        assert otel.record_usage_on_active_span(input_tokens=90) is False
    finally:
        otel._pipeline = None
        otel._configured = False


def test_an_unknown_operation_is_refused_rather_than_traced():
    """The operation name is what every GenAI dashboard groups by.

    A typo there produces a span that is present, plausible and invisible to
    every query, which is worse than no span at all.
    """
    with pytest.raises(ValueError, match="Unknown GenAI operation"):
        with otel.genai_span("completion"):
            pass


def test_the_four_operation_values_are_the_conventions_own():
    """Read from the installed semconv package, never typed out in the module."""
    assert otel.OPERATION_CHAT == "chat"
    assert otel.OPERATION_EXECUTE_TOOL == "execute_tool"
    assert otel.OPERATION_INVOKE_AGENT == "invoke_agent"
    assert otel.OPERATION_EMBEDDINGS == "embeddings"


def test_no_endpoint_means_no_export_and_that_is_a_declared_state(monkeypatch):
    """The state local dev and this suite run in, asserted rather than assumed."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    otel._pipeline = None
    otel._configured = False
    try:
        assert otel.configure_from_environment() is False
        assert otel.is_enabled() is False
        with otel.genai_span(otel.OPERATION_CHAT, request_model="gpt-5.6-terra") as span:
            assert span is None
    finally:
        otel._pipeline = None
        otel._configured = False


def _plain(value):
    """Span attributes hold tuples where the fixture holds JSON arrays."""
    if isinstance(value, (tuple, list)):
        return list(value)
    return value


def _metric_points(reader: InMemoryMetricReader) -> dict:
    collected: dict = {}
    data = reader.get_metrics_data()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                collected[metric.name] = (metric.unit, list(metric.data.data_points))
    return collected

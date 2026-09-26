"""The alarm on a withheld PRISM statement counts the line Siddhi actually writes.

PLAN-p5 P5-D8: an uncited statement no longer fails the scoring task; it is
withheld, the report is flagged, and `prism.statement_withheld_for_review` is
logged at ERROR "which a CloudWatch metric filter and alarm watch". That second
half is Terraform, and a metric filter over a log line is exactly the kind of
coupling that breaks in silence: rename the token and the filter matches
nothing, the metric reads zero, and the alarm reports a healthy system.

So both halves are read here and compared, and the filter is checked against
the log group the scoring task writes to (the on-demand agent, Route.ECS).
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.siddhi import report as siddhi_report

_MAIN_TF = (
    Path(__file__).resolve().parents[2] / "infra" / "modules" / "observability" / "main.tf"
)


def _block(text: str, kind: str, name: str) -> str:
    """The body of `resource "<kind>" "<name>" { ... }`, braces balanced."""
    header = re.search(
        rf'resource\s+"{re.escape(kind)}"\s+"{re.escape(name)}"\s*\{{', text
    )
    assert header is not None, f"{kind}.{name} is not declared in {_MAIN_TF}"
    depth, position = 1, header.end()
    while depth:
        char = text[position]
        depth += {"{": 1, "}": -1}.get(char, 0)
        position += 1
    return text[header.end() : position - 1]


def test_the_metric_filter_matches_the_token_siddhi_logs() -> None:
    body = _block(
        _MAIN_TF.read_text(encoding="utf-8"),
        "aws_cloudwatch_log_metric_filter",
        "prism_statement_withheld",
    )
    pattern = re.search(r'pattern\s*=\s*"((?:[^"\\]|\\.)*)"', body)
    assert pattern is not None
    # The quoted-term form: the whole token, dots included, as one term.
    assert pattern.group(1) == f'\\"{siddhi_report.WITHHELD_LOG_EVENT}\\"'


def test_the_filter_reads_the_log_group_the_scoring_task_writes_to() -> None:
    from app.workers import registry

    assert registry.resolve("pickready.run_functional_assessment").route is registry.Route.ECS
    body = _block(
        _MAIN_TF.read_text(encoding="utf-8"),
        "aws_cloudwatch_log_metric_filter",
        "prism_statement_withheld",
    )
    assert re.search(r"log_group_name\s*=\s*var\.agent_log_group_name\b", body)


def test_one_withheld_statement_pages_somebody() -> None:
    body = _block(
        _MAIN_TF.read_text(encoding="utf-8"),
        "aws_cloudwatch_metric_alarm",
        "prism_statement_withheld",
    )
    assert re.search(
        r"metric_name\s*=\s*aws_cloudwatch_log_metric_filter\.prism_statement_withheld\.",
        body,
    )
    assert re.search(r"threshold\s*=\s*0\b", body)
    assert re.search(r'comparison_operator\s*=\s*"GreaterThanThreshold"', body)
    assert re.search(r"alarm_actions\s*=\s*\[var\.alarm_topic_arn\]", body)
    # Words only in the description an operator reads; no em dash.
    assert chr(8212) not in body

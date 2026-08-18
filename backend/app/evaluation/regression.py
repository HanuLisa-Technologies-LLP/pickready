"""Cases that once failed, kept so they cannot fail again quietly.

WHERE THESE CAME FROM
---------------------
Every entry is a defect that was actually observed, not one that was imagined.
Two of them were found while building the verification engine itself: the
generic-language detector fired on ordinary sentences because the shared
banned-phrase matcher lets a window SHORTER than the banned phrase match, and a
single medium-severity finding failed an entire output because the severity
costs were miscalibrated against the confidence floor.

They are here rather than only in the unit tests because a regression suite
answers a different question. A unit test asks whether a function behaves; this
asks whether the thing that broke before is still fixed, and it runs as one
report somebody reads before a deploy rather than as a green dot among sixteen
hundred others.

ADDING TO THIS FILE
-------------------
When a defect is found in production, add the input that produced it and the
property that should hold. Not the fix, the property. A case written against the
fix passes as soon as the fix exists and stops testing anything the moment the
implementation is replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    #: What went wrong, in the words somebody would search for.
    issue: str
    #: The property that must hold. Returns True when the defect is absent.
    check: Callable[[], bool]
    #: When and where it was observed.
    observed: str


@dataclass(frozen=True)
class RegressionResult:
    case_id: str
    issue: str
    passed: bool
    error: str | None = None


def _generic_detector_ignores_ordinary_prose() -> bool:
    from app.services.verification import generic_language

    ordinary = [
        "We would like to move ahead and will write again with the next step.",
        "When the Kafka partition count changed, what did the rebalance cost you?",
        "Led the shard rebalance after the consumer group stalled.",
    ]
    return not any(generic_language.matched_phrases(text) for text in ordinary)


def _generic_detector_still_catches_filler() -> bool:
    from app.services.verification import generic_language

    return bool(generic_language.matched_phrases("A proven track record and a team player."))


def _one_medium_finding_does_not_fail_an_output() -> bool:
    from app.services.verification import base

    return base.verdict("v", [base.medium("i", "l", "d", "r")]).passed


def _two_medium_findings_do_fail_an_output() -> bool:
    from app.services.verification import base

    return not base.verdict("v", [base.medium("i", "l", "d", "r")] * 2).passed


def _one_high_finding_fails_an_output() -> bool:
    from app.services.verification import base

    return not base.verdict("v", [base.high("i", "l", "d", "r")]).passed


def _tool_attempt_fits_inside_its_deadline() -> bool:
    from app.services.tools import registry

    # The property, not the implementation: a single attempt at the per-attempt
    # timeout must be able to finish inside the total deadline.
    return all(
        spec.timeout_seconds <= spec.deadline_seconds for spec in registry.specs()
    )


def _live_transcripts_are_never_cached() -> bool:
    from app.services.tools import registry

    spec = registry.get("extract_assessment")
    return spec is not None and not spec.idempotent and spec.cache_ttl_seconds == 0


def _routes_agree_with_the_permission_matrix() -> bool:
    from app.services.orchestration import router

    return not router.validate_routes()


def _keyword_retrieval_does_not_require_every_query_term() -> bool:
    from app.services.rag import retrieval

    tsquery = retrieval._tsquery("kafka partition rebalance migration")
    return " | " in tsquery and "&" not in tsquery


def _pii_masking_handles_a_card_before_a_phone() -> bool:
    from app.services.safety import pii

    # A 16-digit card matches the generic long-number rule too. Masked as a card
    # it keeps its last four; masked as a phone it would keep a different four.
    masked = pii.mask_text("card 4111 1111 1111 1234")
    return masked.endswith("1234") and "4111" not in masked


CASES: tuple[RegressionCase, ...] = (
    RegressionCase(
        "generic-language-false-positive",
        "the filler detector fired on ordinary sentences",
        _generic_detector_ignores_ordinary_prose,
        "2026-08-18, while building the verification engine: the shared "
        "banned-phrase matcher lets a window shorter than the phrase match, so "
        "the word we matched the phrase well rounded",
    ),
    RegressionCase(
        "generic-language-still-detects",
        "the fix for the false positives must not disable the detector",
        _generic_detector_still_catches_filler,
        "2026-08-18, paired with the case above",
    ),
    RegressionCase(
        "severity-calibration-single-medium",
        "one medium finding failed an entire output",
        _one_medium_finding_does_not_fail_an_output,
        "2026-08-18, verification severity costs against the confidence floor",
    ),
    RegressionCase(
        "severity-calibration-two-mediums",
        "mediums must still accumulate to a failure",
        _two_medium_findings_do_fail_an_output,
        "2026-08-18, paired with the case above",
    ),
    RegressionCase(
        "severity-high-is-disqualifying",
        "a high-severity finding must fail on its own",
        _one_high_finding_fails_an_output,
        "2026-08-18, the property the calibration must not break",
    ),
    RegressionCase(
        "tool-attempt-fits-deadline",
        "a bounded attempt must fit inside its total deadline",
        _tool_attempt_fits_inside_its_deadline,
        "inherited from the 2026-08-06 loop deadline defect: 24s attempts under "
        "a 26s deadline permitted a 48s request",
    ),
    RegressionCase(
        "transcript-never-cached",
        "a live transcript cached is the wrong assessment scored",
        _live_transcripts_are_never_cached,
        "2026-08-18, tool engine cache policy",
    ),
    RegressionCase(
        "routes-match-permissions",
        "a task routed to an agent that holds no tools fails deep in a call",
        _routes_agree_with_the_permission_matrix,
        "2026-08-18, orchestration router",
    ),
    RegressionCase(
        "keyword-retrieval-or-semantics",
        "the lexical retriever silently matched nothing for ordinary queries",
        _keyword_retrieval_does_not_require_every_query_term,
        "2026-08-18, found by running retrieval against the live index rather "
        "than against a unit test: plainto_tsquery ANDs every term, so one word "
        "absent from the document killed the whole lexical match",
    ),
    RegressionCase(
        "pii-card-before-phone",
        "a card number masked as a phone keeps the wrong four digits",
        _pii_masking_handles_a_card_before_a_phone,
        "2026-08-18, safety PII masker ordering",
    ),
)


def run_all() -> list[RegressionResult]:
    """Run every case. A raising case is a failing case, never a crash."""
    results: list[RegressionResult] = []
    for case in CASES:
        try:
            results.append(RegressionResult(case.case_id, case.issue, bool(case.check())))
        except Exception as exc:  # noqa: BLE001
            results.append(
                RegressionResult(
                    case.case_id, case.issue, False, f"{type(exc).__name__}: {exc}"
                )
            )
    return results


def summary(results: list[RegressionResult]) -> dict[str, Any]:
    failed = [result for result in results if not result.passed]
    return {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": [
            {"case_id": r.case_id, "issue": r.issue, "error": r.error} for r in failed
        ],
    }

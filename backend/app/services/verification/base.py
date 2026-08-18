"""What a verifier IS, and why its verdict is arithmetic rather than a judgement.

A VERIFIER IS A CRITIC THAT PLUGS INTO THE LOOP THAT ALREADY EXISTS
------------------------------------------------------------------
`agent_loop.run_loop` already takes `evaluate` and `verify` callbacks and
already feeds a rejection back to the next attempt verbatim. So a verifier here
is not a second framework running beside that one: it is a function producing a
`Verdict`, and `Verdict.to_critique()` is what hands it to the loop. Regeneration
therefore costs nothing new -- the loop's `reflect -> improve` step IS the
auto-regeneration the specification asks for, already bounded twice over.

WHY CONFIDENCE IS COMPUTED, NOT ASKED FOR
-----------------------------------------
The obvious design is to ask a model how confident it is in an output. It is
also unfalsifiable: you cannot write a test that says "this output scores below
0.7", only one that says "the judge usually says so". Worse, it fails exactly
when the provider is down, which is the moment a guard is worth having. So
`confidence` here is a deterministic function of the findings -- severity counts
in, one number out -- which means it is testable offline, it never costs a
model call, and a reviewer can reconstruct any value by hand.

FINDINGS ARE WRITTEN AS INSTRUCTIONS
------------------------------------
`recommendation` is phrased as the thing the next attempt should DO, because
that string is what `agent_loop` appends to the retry verbatim. "Ground each
probe in a sentence the candidate actually wrote" is a defect a model fixes when
told. "Probes are too generic" is a complaint it cannot act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services import agent_loop

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

#: What each severity costs against a starting confidence of 1.0, read against
#: CONFIDENCE_FLOOR below. One high finding is disqualifying on its own. Two
#: mediums are, and one is not: a single word-count miss is worth telling the
#: next attempt about and is not worth discarding an otherwise sound output
#: over, while two independent things being wrong is a pattern. Lows are
#: recorded and never fail an output by themselves. The numbers are the policy,
#: in one readable place, rather than a threshold rediscovered inside four
#: verifiers.
_SEVERITY_COST = {SEVERITY_HIGH: 1.0, SEVERITY_MEDIUM: 0.2, SEVERITY_LOW: 0.05}

#: Confidence at or below which an output must not ship unreviewed. Matches the
#: `agent_loop` posture: a bounded retry is cheap, a wrong grade on a report a
#: client reads is not.
CONFIDENCE_FLOOR = 0.7


@dataclass(frozen=True)
class Finding:
    """One thing wrong with an output, and what to do about it."""

    severity: str
    issue: str
    location: str
    detail: str
    recommendation: str

    def as_defect(self) -> agent_loop.Defect:
        return agent_loop.Defect(self.issue, self.location, self.recommendation)

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "issue": self.issue,
            "location": self.location,
            "detail": self.detail,
            "recommendation": self.recommendation,
        }


def high(issue: str, location: str, detail: str, recommendation: str) -> Finding:
    return Finding(SEVERITY_HIGH, issue, location, detail, recommendation)


def medium(issue: str, location: str, detail: str, recommendation: str) -> Finding:
    return Finding(SEVERITY_MEDIUM, issue, location, detail, recommendation)


def low(issue: str, location: str, detail: str, recommendation: str) -> Finding:
    return Finding(SEVERITY_LOW, issue, location, detail, recommendation)


@dataclass(frozen=True)
class Verdict:
    """The result of verifying one output.

    `passed` is not simply `not findings`. A low-severity finding is worth
    recording and is not worth spending a regeneration on, so an output may
    pass while carrying findings, and a reader of the verdict can still see
    everything that was noticed.
    """

    verifier: str
    findings: tuple[Finding, ...] = ()

    @property
    def confidence(self) -> float:
        """1.0 minus the accumulated cost of every finding, floored at 0."""
        cost = sum(_SEVERITY_COST.get(f.severity, _SEVERITY_COST[SEVERITY_MEDIUM]) for f in self.findings)
        return round(max(0.0, 1.0 - cost), 4)

    @property
    def passed(self) -> bool:
        return self.confidence > CONFIDENCE_FLOOR

    def by_severity(self, severity: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    def to_critique(self) -> agent_loop.Critique:
        """Hand this verdict to `agent_loop` as an accept/reject with reasons.

        Every finding travels, not only the disqualifying ones: an attempt being
        regenerated for a high-severity defect may as well be told about the
        medium ones in the same breath, and the loop is bounded either way.
        """
        if self.passed:
            return agent_loop.ok()
        return agent_loop.reject_defects(*(f.as_defect() for f in self.findings))

    def as_dict(self) -> dict[str, object]:
        return {
            "verifier": self.verifier,
            "passed": self.passed,
            "confidence": self.confidence,
            "findings": [f.as_dict() for f in self.findings],
        }


def verdict(name: str, findings: Iterable[Finding]) -> Verdict:
    return Verdict(name, tuple(findings))


def combine(name: str, verdicts: Iterable[Verdict]) -> Verdict:
    """Merge several verdicts into one.

    Findings concatenate, so cost accumulates across verifiers rather than
    resetting per verifier. An output that is individually borderline on three
    separate checks is not a passing output.
    """
    merged: list[Finding] = []
    for item in verdicts:
        merged.extend(item.findings)
    return Verdict(name, tuple(merged))


def words_in(text: str) -> int:
    return len(str(text or "").split())

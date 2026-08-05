"""Offline evaluation of the conversational assessment agent.

    python -m app.scripts.eval_interview

WHY THIS EXISTS
---------------
Three times now a change to this agent has been reported working and been
visibly not working: the interview that was "rebuilt on LangGraph" and did not
import LangGraph, the canned acknowledgments that no test asserted the absence
of, and the four keyboard-mash answers that walked straight through to
"Question 8 of 45". Each was caught by a person using the product, not by CI.

The common cause is that the agent's quality lives in JUDGEMENT -- does it
notice a non-answer, does it stay on the competency, does it avoid repeating
itself -- and judgement had no measurement. A unit test pins one case. This
measures the behaviour across a labelled set and reports rates, so a regression
shows up as a number moving rather than as a customer noticing.

WHY IT IS OFFLINE AND DETERMINISTIC
-----------------------------------
Every model call is stubbed. That is deliberate and is the difference between
an eval that runs on every commit and one that nobody runs:

  * it costs nothing and needs no API key, so CI can gate on it;
  * it is reproducible, so a rate that moves means the CODE changed, not that a
    provider was sampling differently that afternoon;
  * it exercises the paths that matter most and are hardest to reach live --
    every degradation branch, which is precisely where the product's promises
    about outages are either true or not.

What it deliberately does NOT measure is whether a real model writes a GOOD
question. That needs a live model and a human, and pretending a stub can judge
it would be the same false confidence this file exists to remove. What it
measures is that the agent does the right thing with whatever the model returns,
including nothing.

READ THE RATES, NOT JUST THE EXIT CODE
--------------------------------------
The thresholds in `test_interview_eval.py` are deliberately set where they are
today, not aspirationally. A rate that is allowed to fall silently is a rate
nobody is defending.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import answer_classification, interviewer

# ── The labelled set ─────────────────────────────────────────────────────────

#: Real production non-answers, kept verbatim. The first four are the exact
#: strings a candidate typed on 2026-08-05, each of which was met with the next
#: scripted question.
NON_ANSWERS: tuple[str, ...] = (
    "fsjdemd",
    "xdshfjg,uyytrs",
    "dwrhejyrkhfbgertyfg",
    "cvdgrertykfmhgnfrshfmgc",
    "ewidjverip",
    "asdf",
    "...",
    "",
    "   ",
    "ok",
    "yes",
    "n/a",
)

#: Answers that are REAL and must never be challenged. The negative answers are
#: the important ones: "I have not used X" is a complete answer and is scored
#: low on its merits by the rubric, never discarded as a non-answer. A false
#: positive here silently grades a real candidate Not Matching, which is far
#: worse than letting a weak answer through to be judged.
REAL_ANSWERS: tuple[str, ...] = (
    "I have not used Kafka in production.",
    "I have never worked with Kubernetes, only Docker Compose.",
    "No, that was handled by a different team.",
    "I built the billing pipeline and cut p99 latency from 900ms to 180ms.",
    "We used Postgres because the data was relational and we needed real "
    "transactions across three tables.",
    "The deploy was a shitshow, so I added a smoke test that ran before "
    "traffic shifted.",
    "About two years, mostly on the ingestion side.",
)

#: Questions an interviewer may legitimately ask that CONTAIN numbers. The
#: no-numbers-to-a-client rule is about scores and grades, never about technical
#: content, and a guard that cannot tell them apart mangles the interview.
LEGITIMATE_NUMERIC_QUESTIONS: tuple[str, ...] = (
    "How did you bring p99 latency under 200ms?",
    "Tell me about the 7-microservice workflow you built.",
    "You mentioned scaling to 10,000 requests per second. How?",
    "What changed between v1 and v2 of that API?",
)


@dataclass
class Result:
    """One measured behaviour, with the cases that failed kept for reading."""

    name: str
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def record(self, ok: bool, case: str) -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(case)


# ── Model stubs ──────────────────────────────────────────────────────────────


def _stub(payload: Any) -> Callable:
    """A model that always returns the same body."""

    async def _call(*args, **kwargs):
        return json.dumps(payload)

    return _call


def _dead() -> Callable:
    """A model that is down. The most important stub in this file: every promise
    the product makes about outages is only true on this path."""

    async def _call(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    return _call


# ── The measurements ─────────────────────────────────────────────────────────


async def _measure_non_answer_detection(monkey) -> Result:
    """Every non-answer must be caught, and no real answer may be."""
    result = Result("non_answer_detection")
    # The deterministic pass must not need the model at all, so a dead model is
    # the honest stub: anything it catches, it catches during an outage too.
    monkey(_dead())
    for answer in NON_ANSWERS:
        verdict = await answer_classification.classify(
            session=None, question="Describe a system you designed.",
            answer=answer, transcript=None,
        )
        result.record(verdict.needs_rechallenge, f"missed non-answer: {answer!r}")
    return result


async def _measure_real_answer_safety(monkey) -> Result:
    """The false-positive direction, which is the expensive one."""
    result = Result("real_answer_not_challenged")
    monkey(_stub({"label": "substantive", "confidence": "high", "reason": ""}))
    for answer in REAL_ANSWERS:
        verdict = await answer_classification.classify(
            session=None, question="Tell me about your last role.",
            answer=answer, transcript=None,
        )
        result.record(
            not verdict.needs_rechallenge and verdict.scorable,
            f"challenged a real answer: {answer!r} -> {verdict.label}",
        )
    return result


async def _measure_outage_degradation(monkey) -> Result:
    """With the model down, the agent must degrade to the product's previous
    behaviour and never to a wrong one."""
    result = Result("degrades_safely")
    monkey(_dead())

    # A real answer must not be classified as anything punitive during an outage.
    verdict = await answer_classification.classify(
        session=None, question="Tell me about Kafka.",
        answer="I ran a three-broker cluster for two years.", transcript=None,
    )
    result.record(
        verdict.label == "substantive" and not verdict.needs_rechallenge,
        f"outage produced a punitive label: {verdict.label}",
    )

    # A generated question must fall back to the stored one, never to nothing.
    stored = "Describe a system you designed end to end."
    delivered = await interviewer.compose_next_question(
        session=None, question=stored,
        transcript=[{"speaker": "candidate", "content": "I led the rewrite."}],
        mode=interviewer.MODE_GENERATE, competency="Systems Design",
    )
    result.record(delivered == stored, f"lost the question on outage: {delivered!r}")

    # A follow-up must simply not happen.
    probe = await interviewer.next_follow_up(
        session=None, question=stored, answer="I led the billing rewrite.",
        transcript=[], follow_ups_used=0, already_followed_up=False,
    )
    result.record(probe is None, f"invented a probe during an outage: {probe!r}")

    # A non-answer must still be challenged, in words, with no model available.
    challenge = await interviewer.challenge_non_answer(
        session=None, question=stored, answer="fsjdemd", transcript=[],
        label="gibberish",
    )
    result.record(bool(challenge), "went silent on gibberish during an outage")
    return result


async def _measure_question_integrity(monkey) -> Result:
    """A rewritten technical question must still be the same question, and a
    generated one must not repeat ground already covered."""
    result = Result("question_integrity")

    # REWORD: dropping the named technology would grade the answer against a
    # rubric for a question nobody was asked.
    monkey(_stub({"question": "How did you tune the message queue when it got slow?"}))
    stored = "Describe how you tuned Kafka consumer lag under load."
    delivered = await interviewer.compose_next_question(
        session=None, question=stored,
        transcript=[{"speaker": "candidate", "content": "We had throughput issues."}],
        mode=interviewer.MODE_REWORD,
    )
    result.record(
        delivered == stored,
        "accepted a reword that dropped the technology it was scored on",
    )

    # REWORD: a faithful rewrite SHOULD be accepted, or the graph has quietly
    # reduced itself to the stored text it replaced.
    good = (
        "You mentioned throughput earlier, so how did you tune Kafka consumer "
        "lag under load?"
    )
    monkey(_stub({"question": good}))
    delivered = await interviewer.compose_next_question(
        session=None, question=stored,
        transcript=[{"speaker": "candidate", "content": "We had throughput issues."}],
        mode=interviewer.MODE_REWORD,
    )
    result.record(delivered == good, "rejected a faithful rewrite")

    # GENERATE: a question already asked must not be asked again.
    asked = "Tell me about the billing pipeline you built at Acme."
    monkey(_stub({"question": "Tell me about the billing pipeline you built at Acme."}))
    delivered = await interviewer.compose_next_question(
        session=None, question="Describe a system you owned.",
        transcript=[{"speaker": "agent", "content": asked}],
        mode=interviewer.MODE_GENERATE, competency="Systems Design",
        asked_before=[asked],
    )
    result.record(delivered != asked, "asked a question it had already asked")
    return result


async def _measure_no_praise(monkey) -> Result:
    """No templated acknowledgment reaches a candidate, whoever wrote it. The
    product's own canned openers are gone; a model at temperature 0.7 writes
    them unprompted, which reads identically."""
    result = Result("no_praise")
    for opener in ("Great! ", "Understood, ", "Thanks. ", "Perfect! ", "Nice, "):
        monkey(_stub({"follow_up": f"{opener}what broke first?"}))
        probe = await interviewer.next_follow_up(
            session=None, question="Describe a system you designed.",
            answer="I built the billing pipeline and cut p99 to 180ms.",
            transcript=[], follow_ups_used=0, already_followed_up=False,
        )
        result.record(
            bool(probe) and not probe.lower().startswith(opener.strip().lower()[:4]),
            f"praise survived: {probe!r}",
        )
    return result


def _measure_budget_determinism() -> Result:
    """The coverage plan and the budget are the reproducible half of the agent.
    Same interview, same ceiling, every time."""
    result = Result("deterministic_budget")
    for count in (10, 15, 20, 22, 45, 45, 45):
        budgets = {interviewer.follow_up_budget(count) for _ in range(5)}
        result.record(len(budgets) == 1, f"budget not deterministic at {count}")
    result.record(
        interviewer.follow_up_budget(45) == 15
        and interviewer.follow_up_budget(3) == interviewer.MAX_FOLLOW_UPS,
        "budget is not clamped where it was documented to be",
    )
    return result


# ── Runner ───────────────────────────────────────────────────────────────────


async def run() -> list[Result]:
    """Every measurement, with the model stubbed per measurement."""
    import app.services.answer_classification as ac
    import app.services.interviewer as iv

    originals = (ac.llm_router.invoke_llm, iv.llm_router.invoke_llm)

    def monkey(fn):
        ac.llm_router.invoke_llm = fn
        iv.llm_router.invoke_llm = fn

    try:
        results = [
            await _measure_non_answer_detection(monkey),
            await _measure_real_answer_safety(monkey),
            await _measure_outage_degradation(monkey),
            await _measure_question_integrity(monkey),
            await _measure_no_praise(monkey),
            _measure_budget_determinism(),
        ]
    finally:
        # Restored even on failure: leaving a stub installed would silently
        # break every test that ran after this one in the same process.
        ac.llm_router.invoke_llm, iv.llm_router.invoke_llm = originals
    return results


def report(results: list[Result]) -> str:
    lines = ["", "PickReady interview agent, offline evaluation", ""]
    for item in results:
        mark = "PASS" if item.rate == 1.0 else "FAIL"
        lines.append(f"  [{mark}] {item.name}: {item.passed}/{item.total}")
        for failure in item.failures:
            lines.append(f"           {failure}")
    worst = min((item.rate for item in results), default=0.0)
    lines.append("")
    lines.append(f"  lowest rate: {worst:.2f}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = asyncio.run(run())
    print(report(results))
    return 0 if all(item.rate == 1.0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

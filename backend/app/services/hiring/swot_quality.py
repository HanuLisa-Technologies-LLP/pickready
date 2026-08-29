"""LAYER 3's quality control: the seven probes and the six refusals.

PROVENANCE: RPN-PHIL-001 §18.3 (the seven high-value probes), §18.5 (the
rejection rules), §20.2 (the six-competency ceiling) and §20.3 (force-ranking).

  * the SEVEN HIGH-VALUE PROBES (§18.3), each carrying the Runbook's own name
    and its own stated purpose, and
  * the SIX REJECTION RULES (§18.5) -- reject back to the Hiring Manager if
    weaknesses are absent or purely external, every competency is marked
    must-have, requirements are traits rather than observable evidence, a
    prohibited disqualifier appears, THE STATED REQUIREMENTS WOULD EXCLUDE THE
    HIRING MANAGER'S OWN CURRENT BEST PERFORMER, or the situation type cannot
    be determined.

The best-performer test was absent before the Runbook was read, and it is the
one §18.5 singles out: "a devastating and highly effective test -- run it". It
is also the only rule in the section that catches a requirement set which is
internally coherent and still wrong, which is the failure the other five cannot
see. Nobody writes a requirement they believe excludes their best engineer; they
write six requirements that jointly do.

WHY REJECTION IS THE VALUABLE HALF
------------------------------------
A SWOT session that accepts whatever it is given produces a matrix that looks
complete and grades nobody usefully. The specific failures below are not
hypothetical -- they are what a busy hiring manager doing an unpaid step in
their own hiring process actually produces when nothing pushes back:

  * WEAKNESSES ABSENT OR EXTERNAL-ONLY. "The market is tight" and "salaries are
    high" are threats to hiring, not weaknesses in the role. A role with no
    internal weakness is a role nobody has thought about failing in, and
    failure modes are where the discriminating criteria come from.
  * EVERYTHING IS MUST-HAVE. A matrix where every item caps the report is a
    matrix that grades every imperfect candidate the same, which is the same as
    not grading.
  * TRAITS RATHER THAN EVIDENCE. "Ownership mindset" produces a competency
    nobody can evidence, which produces a grade nobody can defend. Identical
    argument to the Company DNA instrument's Section 3, and it uses the same
    detector -- one rule, one implementation.
  * A PROHIBITED DISQUALIFIER. Unlawful, and it must be refused at the moment
    it is typed rather than at compilation, because by then the sentence the
    manager wrote is gone and only the compiled artifact remains.
  * THE REQUIREMENTS WOULD EXCLUDE THEIR OWN BEST PERFORMER. The one refusal
    that catches a coherent requirement set rather than a malformed one, and
    the only one that cannot be computed -- the platform does not have the
    manager's current team, and a model asked to guess would be inventing a
    person to fail a test. So it is ASKED, and an unanswered test is recorded
    as outstanding rather than as passed.
  * SITUATION TYPE UNDETERMINABLE. Named in §18.4 as the most expensive error
    available at intake, because it re-weights the whole matrix coherently and
    invisibly.

REJECTION IS A CONVERSATION, NOT AN ERROR
-------------------------------------------
Every refusal returns the SENTENCE Bodha should say, not an error code. The
hiring manager is not a user submitting a malformed form; they are a busy person
being asked to think harder about something, and the difference between "invalid
input" and "what would you see them doing?" is the difference between a session
that improves and one that gets abandoned.

NOTHING HERE CALLS A MODEL
---------------------------
Same reason the gates do not: a quality check that needs a provider fails open
exactly when the provider is down, and an intake accepted during an outage
produces a matrix that grades candidates for the life of the job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.services.hiring import company_dna, situations

__all__ = [
    "HIGH_VALUE_PROBES",
    "probe_for",
    "trade_off_question",
    "REJECTION_RULES",
    "excludes_best_performer",
    "Rejection",
    "QualityReport",
    "review",
    "MAX_MUST_HAVE_SHARE",
    "MIN_INTERNAL_WEAKNESSES",
]

# ── The seven high-value probes (§18.3) ──────────────────────────────────────
#
# RPN-PHIL-001 §18.3 NAMES all seven and gives each one's question. The names
# are the Runbook's own ("the empty-seat probe", "the first-90-days probe") and
# are used as the keys, so a probe can be traced to its line in the document
# without a translation table.
#
# WHAT RECONCILIATION CHANGED. The pre-Runbook set had seven probes and five of
# them asked something else:
#
#   empty-seat      "What is not getting done right now?"
#                   was absent; nothing asked what work is currently undone
#   first-90-days   "What would this person deliver in their first quarter?"
#                   was "six months in, what has happened" -- a different
#                   horizon, and §18.3 ties the 90-day answer directly to
#                   must-have competencies
#   last-person     "Why did the previous person leave or fail?"
#                   was close, as "the last person who did not work out"
#   rejection       "Describe a candidate who looked perfect on paper that
#                   you'd still say no to. Why?"
#                   was absent, and it is the one §18.3 says surfaces unstated
#                   criteria BEFORE they become invisible filters
#   trade-off       "If you could only have deep X or deep Y, which?"
#                   was present, hardcoded to technical-versus-business rather
#                   than parameterised on the role's own competencies
#   scale-reality   "What size and messiness of system/team/budget will they
#                   actually face?"
#                   was absent; "what will make this job harder" is a different
#                   question and does not prevent a scope mismatch
#   autonomy        "How much direction will they get?"
#                   was absent as a probe, though §18.2 and Appendix B4 both
#                   list it, and §18.3 says it determines how heavily to weight
#                   self-direction evidence
#
# The one that matters most is the REJECTION PROBE. It is the only instrument in
# the session designed to surface a criterion the hiring manager has not
# declared, and an undeclared criterion is precisely what becomes an invisible
# filter later. Its absence meant the session had no way to find one.
#
# Each probe's `purpose` is §18.3's own stated purpose, not a paraphrase.


@dataclass(frozen=True)
class Probe:
    key: str
    question: str
    #: The Runbook's name for this probe, used in the session transcript so a
    #: reviewer reading a transcript can find the section that governs it.
    name: str
    #: What this probe is for, in §18.3's own words. Not shown to the hiring
    #: manager -- it is here so the next person to edit the wording knows what
    #: they would be breaking.
    purpose: str
    #: Which SWOT area it deepens.
    area: str
    source: str = "RPN-PHIL-001 §18.3"


HIGH_VALUE_PROBES: tuple[Probe, ...] = (
    Probe(
        key="empty_seat",
        name="The empty-seat probe",
        question="What is not getting done right now?",
        purpose="Converts abstract requirements into concrete work.",
        area="weaknesses",
    ),
    Probe(
        key="first_90_days",
        name="The first-90-days probe",
        question="What would this person deliver in their first quarter?",
        purpose="Converts to must-have competencies immediately.",
        area="weaknesses",
    ),
    Probe(
        key="last_person",
        name="The last-person probe",
        question="Why did the previous person leave or fail?",
        purpose="Produces the most honest threat data available.",
        area="threats",
    ),
    Probe(
        key="rejection",
        name="The rejection probe",
        question=(
            "Describe a candidate who looked perfect on paper that you would "
            "still say no to. Why?"
        ),
        purpose=(
            "Surfaces unstated criteria before they become invisible filters."
        ),
        area="threats",
    ),
    Probe(
        key="trade_off",
        name="The trade-off probe",
        question="If you could only have deep {left} or deep {right}, which?",
        purpose="This is the force-ranking, extracted conversationally.",
        area="strengths",
    ),
    Probe(
        key="scale_reality",
        name="The scale-reality probe",
        question=(
            "What size and messiness of system, team or budget will they "
            "actually face?"
        ),
        purpose="Prevents scope mismatches.",
        area="opportunities",
    ),
    Probe(
        key="autonomy",
        name="The autonomy probe",
        question="How much direction will they get?",
        purpose="Determines how heavily to weight self-direction evidence.",
        area="strengths",
    ),
)


_PROBES_BY_KEY: dict[str, Probe] = {probe.key: probe for probe in HIGH_VALUE_PROBES}


def trade_off_question(left: str, right: str) -> str:
    """§18.3's trade-off probe, filled in with this role's own two competencies.

    PARAMETERISED RATHER THAN FIXED, because the Runbook writes it as
    "deep X or deep Y" and Appendix B6 says to "repeat until the ranking is
    stable". A probe hardcoded to one pair of competencies can be asked once and
    force-ranks nothing; the whole point of the instrument is that it is asked
    repeatedly against whichever two items are still tied.
    """
    left = (left or "").strip()
    right = (right or "").strip()
    if not left or not right:
        raise ValueError(
            "The trade-off probe compares two named competencies. Asking it "
            "with one side blank produces a leading question, not a "
            "force-ranking."
        )
    return _PROBES_BY_KEY["trade_off"].question.format(left=left, right=right)

_PROBES_BY_AREA: dict[str, tuple[Probe, ...]] = {}
for _probe in HIGH_VALUE_PROBES:
    _PROBES_BY_AREA[_probe.area] = _PROBES_BY_AREA.get(_probe.area, ()) + (_probe,)


def probe_for(area: str, *, asked: Iterable[str] = ()) -> Probe | None:
    """The next unasked high-value probe for an area, or None.

    Ordered rather than random: the probes build on each other, and a session
    that asks "what would make this harder" before "who is this replacing" gets
    a worse answer to both.
    """
    already = set(asked)
    for probe in _PROBES_BY_AREA.get(area, ()):
        if probe.key not in already:
            return probe
    return None


# ── The six rejection rules (§18.5) ──────────────────────────────────────────
#
# SIX, NOT FIVE. RPN-PHIL-001 §18.5 lists six triggers and the pre-Runbook
# implementation had five of them. The missing one is the best-performer test,
# and the Runbook is emphatic about it in a way it is about nothing else in the
# section: "a devastating and highly effective test -- run it".
#
# It is the only rule in §18.5 that catches a requirement set which is
# INTERNALLY COHERENT and still wrong. The other five catch a malformed intake:
# no weaknesses, everything essential, adjectives, an unlawful filter, an
# undeterminable situation. The best-performer test catches a well-formed intake
# whose bar the hiring manager's own strongest person would fail, which is the
# most common way a real scorecard goes wrong and the hardest to see from
# inside the session. Nobody writes a requirement they believe excludes their
# best engineer; they write six requirements that jointly do.
#
# It is asked, not inferred. There is no way to compute it -- the platform does
# not have the hiring manager's current team, and a model asked to guess would
# be inventing a person to fail a test. So `excludes_best_performer` takes the
# manager's own answer, and `review` refuses an intake where the answer is yes
# and abstains where it was never asked rather than treating silence as a pass.

#: Above this share of Must-have items, the matrix stops discriminating.
#:
#: Two thirds, not a half. A genuinely demanding role legitimately has more
#: essentials than nice-to-haves, and refusing that would be the platform
#: telling a hiring manager they are wrong about their own job. What it cannot
#: be is EVERYTHING -- at which point every imperfect candidate grades the same
#: and the matrix has stopped doing its one job.
#:
#: RUNBOOK-AMBIGUITY (§18.5, §20.3): §18.5's trigger is "every competency is
#: marked must-have" and §20.3 requires a force-ranking with no ties, but
#: neither states a share at which an intake is handed back. This threshold is
#: therefore this implementation's, and the direction chosen is the one that
#: refuses more. Recorded in RUNBOOK_OPEN_QUESTIONS_PHASE0B.md.
MAX_MUST_HAVE_SHARE = 0.67

#: A role with no internal weakness is a role nobody has thought about failing
#: in. One is a low bar and it is the bar: the aim is to make the manager think
#: of one, not to interrogate them.
MIN_INTERNAL_WEAKNESSES = 1

#: §20.2, the six-competency ceiling. A scorecard selects at most six items from
#: the department menu; Part VI states it as a universal rule across every
#: department, and Appendix B6 asks the hiring manager to "rank the required
#: competencies 1..n (max 6). No ties."
MAX_SCORECARD_COMPETENCIES = 6

#: §18.5's six triggers, in the Runbook's printed order, as addressable rules.
#:
#: Data rather than six inline branches, because "how many rules does §18.5
#: have and does this code implement all of them" is exactly the question that
#: went unanswered for the life of the previous implementation. A list that can
#: be counted is a list whose length can be asserted.
REJECTION_RULES: tuple[tuple[str, str], ...] = (
    ("weaknesses_absent", "Weaknesses are absent"),
    ("weaknesses_external_only", "Weaknesses are purely external"),
    ("everything_is_must_have", "Every competency is marked must-have"),
    ("trait_not_evidence", "Requirements are traits, not observable evidence"),
    ("prohibited_disqualifier", "A prohibited disqualifier appears"),
    (
        "excludes_best_performer",
        "The stated requirements would exclude the hiring manager's own "
        "current best performer",
    ),
    ("situation_undeterminable", "The situation type cannot be determined"),
)

#: Phrases that describe the MARKET rather than the ROLE. A weakness list
#: containing only these is an external-only list, which §18.5 names explicitly
#: as a rejection trigger and illustrates with "the market is competitive".
_EXTERNAL_ONLY: tuple[str, ...] = (
    "market", "salary", "salaries", "compensation", "budget", "notice period",
    "competition", "competitors", "hiring is hard", "candidates are",
    "talent is scarce", "shortage", "attrition in the industry", "economy",
    "location", "commute", "relocation", "visa",
)


def excludes_best_performer(answer: Any) -> bool | None:
    """Did the hiring manager say their own best performer would be excluded?

    Three-valued on purpose, and the third value is the point: True refuses the
    intake, False accepts it, and None means the question was never put, which
    is a different state from "no" and must not be collapsed into one. An
    unasked §18.5 test that read as a pass would let the most effective rule in
    the section be satisfied by never running it, which is the failure mode the
    Runbook's own instruction ("run it") guards against.
    """
    if answer is None:
        return None
    if isinstance(answer, bool):
        return answer
    text = str(answer).strip().lower()
    if not text:
        return None
    if text in ("yes", "y", "true", "they would", "probably", "some of them"):
        return True
    if text in ("no", "n", "false", "they would not", "no they would not"):
        return False
    return None


@dataclass(frozen=True)
class Rejection:
    """One reason the intake is being handed back, and what to say.

    `say` is a SENTENCE, not an error code. The hiring manager is a busy person
    being asked to think harder, and "what would you see them doing?" produces a
    better second answer than "validation failed" does.
    """

    rule: str
    say: str
    #: Which SWOT area to reopen, when the refusal is about one.
    area: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "area": self.area}


@dataclass
class QualityReport:
    rejections: list[Rejection] = field(default_factory=list)
    #: §18.5 checks that could not be RUN, as distinct from checks that passed.
    #: A test nobody asked is not a test somebody passed, and collapsing the two
    #: would make skipping a rule indistinguishable from satisfying it.
    outstanding: list[str] = field(default_factory=list)
    #: Situation types the signals point at, strongest first. Empty means the
    #: type could not be determined, which is itself a rejection.
    situation_candidates: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.rejections

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "rejections": [r.as_dict() for r in self.rejections],
            "outstanding": list(self.outstanding),
            "situation_candidates": list(self.situation_candidates),
        }


def _is_external_only(points: Sequence[str]) -> bool:
    """True when every weakness names the market rather than the role.

    ALL, not any. A manager who says "the market is tight AND the codebase is
    ten years old" has given a real internal weakness, and refusing them for
    also mentioning the market would be pedantry that gets the session
    abandoned.
    """
    real = [p for p in points if str(p or "").strip()]
    if not real:
        return False
    return all(
        any(token in str(point).lower() for token in _EXTERNAL_ONLY) for point in real
    )


def review(
    captured: Mapping[str, Sequence[str]],
    *,
    categories: Sequence[str] = (),
    disqualifiers: Sequence[str] = (),
    situation_key: str | None = None,
    best_performer_excluded: Any = None,
) -> QualityReport:
    """Apply all six §18.5 rules. Returns what to say, never raises.

    `categories` is the proposed per-item category list, used for the
    everything-is-must-have check. `situation_key` is the classification Bodha
    proposed; when it is None the signals are counted deterministically as a
    fallback, so a provider outage costs the model's classification and not the
    session. `best_performer_excluded` is the hiring manager's own answer to
    §18.5's best-performer test, and None means it has not been asked yet --
    which is recorded as an OUTSTANDING check rather than as a pass.
    """
    report = QualityReport()
    weaknesses = list(captured.get("weaknesses") or [])

    # ── Rule 1: weaknesses absent ───────────────────────────────────────────
    if len([w for w in weaknesses if str(w or "").strip()]) < MIN_INTERNAL_WEAKNESSES:
        report.rejections.append(
            Rejection(
                rule="weaknesses_absent",
                area="weaknesses",
                say=(
                    "I do not have anything yet on where this role goes wrong. "
                    "Think of the last person who did not work out in this job, "
                    "or one like it -- what was the first concrete sign?"
                ),
            )
        )
    # ── Rule 2: weaknesses external-only ────────────────────────────────────
    elif _is_external_only(weaknesses):
        report.rejections.append(
            Rejection(
                rule="weaknesses_external_only",
                area="weaknesses",
                say=(
                    "Everything you have given me about weaknesses is about the "
                    "market rather than the job itself. Those are real, and they "
                    "are not something a candidate can be assessed on. What is "
                    "hard about the role once someone is in it?"
                ),
            )
        )

    # ── Rule 3: everything is must-have ─────────────────────────────────────
    listed = [str(c or "").strip() for c in categories if str(c or "").strip()]
    if listed:
        share = sum(1 for c in listed if c == "must_have") / len(listed)
        if share > MAX_MUST_HAVE_SHARE:
            report.rejections.append(
                Rejection(
                    rule="everything_is_must_have",
                    say=(
                        f"Nearly everything here is marked essential. If a "
                        f"candidate can fail any one of them and be out, then "
                        f"every imperfect candidate comes back the same and the "
                        f"assessment stops telling you anything. Which of these "
                        f"would you still interview someone without?"
                    ),
                )
            )

    # ── Rule 4: traits rather than evidence ─────────────────────────────────
    #
    # Uses the SAME detector as the Company DNA instrument's Section 3. One rule,
    # one implementation: two copies of "is this an adjective" would drift, and
    # the drift would be invisible -- one intake accepting what the other refuses.
    for area, points in captured.items():
        for point in points or ():
            text = str(point or "").strip()
            if text and not company_dna.is_observable(text):
                report.rejections.append(
                    Rejection(
                        rule="trait_not_evidence",
                        area=area,
                        say=company_dna.rejection_message(text),
                    )
                )
                # ONE per area. A manager handed six refusals at once stops
                # doing the session, and the first one teaches the pattern.
                break

    # ── Rule 5a: a prohibited disqualifier ──────────────────────────────────
    for entry in disqualifiers:
        offending = company_dna.prohibited_in(str(entry or ""))
        if offending:
            report.rejections.append(
                Rejection(
                    rule="prohibited_disqualifier",
                    say=(
                        "I cannot use that one. A candidate can be ruled out for "
                        "something about the job -- a licence, a certification, a "
                        "legal requirement to work here -- but not for something "
                        "about who they are. Is there a job-related requirement "
                        "behind it?"
                    ),
                )
            )
            break

    # ── Rule 6: the requirements would exclude their own best performer ─────
    #
    # §18.5's most effective trigger and the one the Runbook tells the reader to
    # run: "the stated requirements would exclude the hiring manager's own
    # current best performer (a devastating and highly effective test -- run
    # it)". It is the only rule here that catches a requirement set which is
    # internally coherent and still wrong.
    best_performer = excludes_best_performer(best_performer_excluded)
    if best_performer is True:
        report.rejections.append(
            Rejection(
                rule="excludes_best_performer",
                say=(
                    "You have just told me your own strongest person would not "
                    "get through this list. That is worth taking seriously, "
                    "because whatever it is they do that makes them your best "
                    "is not in the requirements. Which of these would you drop "
                    "to let them through, and what would you add that they "
                    "have?"
                ),
            )
        )
    elif best_performer is None:
        # NOT a rejection, and NOT a pass. An unasked test recorded as passed
        # would let the most effective rule in §18.5 be satisfied by skipping
        # it, so the outstanding check is carried on the report and the session
        # cannot be closed against it silently.
        report.outstanding.append("excludes_best_performer")

    # ── Rule 7: the situation type could not be determined ──────────────────
    #
    # Named in spec-doc5 as the single most expensive error available at intake,
    # because it re-weights the WHOLE matrix coherently and invisibly. So an
    # undeterminable type is a refusal rather than a shrug.
    signals = situations.classify_signals(
        [str(p) for points in captured.values() for p in (points or [])]
    )
    report.situation_candidates = [key for key, _hits, _matched in signals]
    if not situations.is_valid(situation_key) and not report.situation_candidates:
        report.rejections.append(
            Rejection(
                rule="situation_undeterminable",
                area="opportunities",
                say=(
                    "One more thing before I close this off. Is this person "
                    "replacing someone who left, joining alongside people doing "
                    "the same job, fixing something that is going badly, or the "
                    "first to do this here? It changes how the whole assessment "
                    "is weighted, so I would rather ask than guess."
                ),
            )
        )
    return report

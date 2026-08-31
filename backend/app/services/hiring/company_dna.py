"""LAYER 2: the Company Hiring Philosophy, captured once per client.

WHAT THIS IS
------------
The 12-section Company DNA Intake Instrument (spec-doc5 §A.1, citing Runbook
§16), conducted by Bodha with the client's HR Manager or CHRO at onboarding,
before any job exists for that client. Its output is a VERSIONED ARTIFACT --
weight modifiers, evidence requirements, thresholds, disqualifiers, sourcing
instructions and dossier preferences -- stored once and retrieved by every
subsequent job that client posts.

TWO DESIGN RULES THAT ARE NOT NEGOTIABLE
-----------------------------------------

**A STRUCTURED SESSION, NOT A FREE-TEXT FIELD.** spec-doc5 says so explicitly,
and the reason is what happens downstream: Sutra consumes the COMPILED artifact
and "never the client's free-text preferences directly". A free-text field would
put an unbounded client-authored string into a prompt that decides what every
candidate is graded on, which is both an injection surface and, more mundanely,
a way for "we like people who are hungry" to become an evaluation criterion.

**FORCED SCALES IN SECTION 2, OBSERVABLE EVIDENCE IN SECTION 3.** Also stated
explicitly. Section 2 asks for trade-offs on a scale because a free-text answer
to "what do you value" is always "excellence and integrity", which distinguishes
no client from any other and therefore modifies no weight. Section 3 REJECTS an
adjective and asks again: "ownership mindset" is refused, "has taken a project
from an unclear brief to a shipped outcome" is accepted. That rejection is the
instrument's whole value -- an unrejected adjective becomes a competency nobody
can evidence, which becomes a grade nobody can defend.

THE COMPILATION RULE
--------------------
`compile_artifact` turns answers into the six output kinds. It is DETERMINISTIC
and calls no model. That is deliberate and it is the same argument the gates and
the aggregator make: this artifact constrains every job the client will ever
post, so it must be reproducible, diffable between versions, and explainable
without a provider. A model summarising an intake into weights would make "why
is delivery weighted higher for this client" unanswerable.

WHAT THE COMPILATION MAY NOT DO
--------------------------------
Everything it emits passes through `layers.resolve`, so a client cannot weight a
competency to zero, cannot lower a threshold past the platform floor, and cannot
switch off an integrity rule at all. A refusal is RECORDED on the artifact
rather than dropped -- a client who asked to disable the Must-have cap should be
visible to whoever supports them, not silently ignored.

PROVENANCE, AND WHAT RECONCILIATION AGAINST §16 AND APPENDIX A CHANGED
-----------------------------------------------------------------------
The instrument had twelve sections before the Runbook was read and FIVE of
§16's twelve were missing from it entirely:

    §16 S7   Diversity and inclusion commitments
    §16 S8   Data, consent and privacy
    §16 S9   Compensation and offer reality
    §16 S12  Historical calibration data
    §16 S1   present in name, but four of its six fields absent

and four sections existed that §16 does not have (an evidence bar, a threshold
bar, ways-of-working, and a read-back confirmation). Section 2 asked five
trade-offs of which two are §16's; the other three were invented, and two of
§16's six were absent. The scale ran -2..+2 where §16 and Appendix A2 both
print 1..5.

THE MOST CONSEQUENTIAL OMISSION IS S12, HISTORICAL CALIBRATION DATA. §16 calls
it "the highest-value input in the entire intake" and says it is "worth
pursuing hard", because it is the one answer that converts the platform's weight
baselines from professional judgement into evidence about that specific client
(§11.1 is explicit that the baselines are "calibration hypotheses"). An
instrument that never asks for it cannot ever close the calibration loop
Part X is built around, and nothing downstream would have reported the absence.

THE MOST DANGEROUS WAS S7. Without it, the prohibited-filter list was never
CONFIRMED by the client. §12.4 requires that confirmation, and its value is
procedural rather than legal: a client who has signed the list is having a
conversation when they later ask for something on it, and a client who has not
is being told "no" for the first time at the worst moment.

Both §16 Section 3 example pairs are now carried, not one. The second ("Team
player") is the harder teaching example, because its rejected form is a
compliment rather than an abstraction and its accepted form names a structural
condition rather than a nicer adjective.

The evidence-bar section was not merely absent from §16, it INVERTED THE
LAYERING: it asked the client how much corroboration they wanted, where §7.4
sets minimum independent sources by seniority as a Layer 1 floor and C2 states
it as a commitment. A client answering "a convincing account is enough" was
lowering a platform floor through an intake question. `compile_artifact` now
reads §7.4 and the client may only ask for more.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.services.hiring import layers
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)

__all__ = [
    "SECTIONS",
    "EVIDENCE_LIST_QUESTION",
    "QUESTION_KINDS",
    "EvidenceExample",
    "SCALE_NEUTRAL",
    "minimum_independent_groups",
    "SECTION_KEYS",
    "Section",
    "Question",
    "SCALE_QUESTION",
    "EVIDENCE_QUESTION",
    "CHOICE_QUESTION",
    "TEXT_QUESTION",
    "ARTIFACT_VERSION",
    "CompanyDNA",
    "compile_artifact",
    "is_observable",
    "rejection_message",
    "PROHIBITED_DISQUALIFIERS",
    "next_unanswered",
    "completeness",
]

#: Bumped when the SHAPE of a compiled artifact changes, never when a client's
#: answers change (that is the row's own `version`). A consumer reading an
#: artifact written by an older compiler needs to know which shape it is.
ARTIFACT_VERSION = 1


# ── Question kinds ───────────────────────────────────────────────────────────

SCALE_QUESTION = "scale"          # §16 S2's forced scale, 1..5
EVIDENCE_QUESTION = "evidence"    # must be observable; adjectives are rejected
EVIDENCE_LIST_QUESTION = "evidence_list"  # several observable items, one per line
CHOICE_QUESTION = "choice"        # a closed list
TEXT_QUESTION = "text"            # free text, used only where nothing is derived

QUESTION_KINDS: tuple[str, ...] = (
    SCALE_QUESTION,
    EVIDENCE_QUESTION,
    EVIDENCE_LIST_QUESTION,
    CHOICE_QUESTION,
    TEXT_QUESTION,
)


@dataclass(frozen=True)
class EvidenceExample:
    """One of §16 Section 3's accepted/rejected pairs, used as the literal bar.

    Both pairs are carried, not one. The second ("Team player") is the harder
    teaching example: the rejected phrase is a compliment rather than an
    abstraction, and its accepted form names a STRUCTURAL condition -- authority
    the person did not have -- rather than a nicer adjective. A client who has
    only seen the first pair tends to produce a longer adjective on the second
    attempt.
    """

    rejected: str
    accepted: str


@dataclass(frozen=True)
class Question:
    key: str
    kind: str
    prompt: str
    #: SCALE only: what the two poles mean. Written as two real alternatives a
    #: client has to choose between, never as "how important is X" -- everything
    #: is important, so a one-sided scale collects fives and modifies nothing.
    poles: tuple[str, str] | None = None
    #: CHOICE only.
    options: tuple[str, ...] = ()
    #: Which dimension the HIGH end of a SCALE answer moves DOWN. None means the
    #: answer is recorded and modifies no dimension weight, which several
    #: deliberately do: §16 S2 maps two of its six scales to evidence-tier
    #: preference and tenure reading rather than to a weight.
    dimension: str | None = None
    #: The dimension the high end moves UP, where §11.2 pairs two. "We hire for
    #: potential and train" is D5 up AND D2 down, and recording only half of it
    #: would make the weight vector drift in one direction over successive
    #: intakes.
    counter_dimension: str | None = None
    #: Whether the session may close without it.
    required: bool = True
    help_text: str = ""
    #: The Runbook section this question comes from.
    source: str = ""


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    #: Why this section exists, in the terms a CHRO would care about. Shown in
    #: the UI, so it is written for them.
    intent: str
    questions: tuple[Question, ...]
    #: The Runbook section and appendix field this section implements.
    source: str = ""
    #: §16 S3 asks for "five to eight" items and Appendix A3 prints five lines
    #: with room for more. Carried as data because it is a stated quantity.
    min_items: int | None = None
    max_items: int | None = None
    #: The shape §16 asks the client to write an item in.
    item_format: str = ""
    #: The accepted/rejected pairs §16 prints for this section.
    examples: tuple[EvidenceExample, ...] = ()
#: and then weight a matrix by it.
#: §16's scale is 1 to 5 with a real midpoint, and Appendix A2 prints it that
#: way for all six questions ("Proven delivery <-1 ... 5-> Potential").
#:
#: CORRECTED. The first implementation used a signed -2..+2 scale, which is the
#: same five positions relabelled and is not what a client is handed. That
#: mattered for one concrete reason beyond fidelity: a stored answer of 0 means
#: "no preference" on the old scale and is OUT OF RANGE on the Runbook's, so
#: the two cannot be told apart in a column of integers. `SCALE_NEUTRAL` is
#: therefore 3, and it is still a real answer rather than a cop-out -- forcing a
#: client off the midpoint would manufacture a preference and then weight a
#: matrix by it.
SCALE_MIN, SCALE_MAX = 1, 5
SCALE_NEUTRAL = 3


SECTIONS: tuple[Section, ...] = (
    # ── §16 Section 1 / Appendix A1 ─────────────────────────────────────────
    Section(
        key="organisational_context",
        title="Organisational context",
        intent=(
            "Sets the environment every later answer is read against. Two of "
            "these do modify the engine: regulatory exposure drives the "
            "authenticity floor and the credential logic, and your real "
            "interview capacity decides how long a shortlist can usefully be."
        ),
        source="RPN-PHIL-001 §16 S1, Appendix A1",
        questions=(
            Question(
                key="headcount_growth_stage",
                kind=TEXT_QUESTION,
                prompt="Headcount, growth rate, and ownership or funding stage?",
                help_text="Sets the environment-match reference for Role and Context Fit.",
            ),
            Question(
                key="industry_regulatory",
                kind=TEXT_QUESTION,
                prompt="What industry are you in, and what regulatory exposure comes with it?",
                help_text=(
                    "Drives the authenticity floor, background-check "
                    "requirements and credential logic."
                ),
            ),
            Question(
                key="locations_work_model",
                kind=TEXT_QUESTION,
                prompt="Which locations, and what work model at each one?",
            ),
            Question(
                key="attrition",
                kind=TEXT_QUESTION,
                prompt="What is your annual attrition, and where does it concentrate?",
            ),
            Question(
                key="time_to_hire",
                kind=TEXT_QUESTION,
                prompt="What is your actual time to hire by level? Not the target.",
                help_text=(
                    "Sets how much evidence we can realistically collect before "
                    "you need an answer."
                ),
            ),
            Question(
                key="interview_capacity",
                kind=TEXT_QUESTION,
                prompt="How many interviews can you run per role per week?",
                help_text="Determines the shortlist size that is actually usable.",
            ),
        ),
    ),
    # ── §16 Section 2 / Appendix A2, the six forced scales ──────────────────
    Section(
        key="evaluation_philosophy",
        title="Evaluation philosophy",
        intent=(
            "FORCED SCALES, not free text. Asking how much you value excellence "
            "gets a five from everybody and tells us nothing. Asking which of "
            "two good things you would give up tells us what you are really "
            "hiring for."
        ),
        source="RPN-PHIL-001 §16 S2, Appendix A2",
        questions=(
            Question(
                key="proven_vs_potential",
                kind=SCALE_QUESTION,
                prompt="Proven delivery, or potential?",
                poles=("All proven delivery", "Heavy on potential"),
                dimension=DIM_TRACK_RECORD,
                counter_dimension=DIM_TRAJECTORY,
                source="RPN-PHIL-001 §16 S2, §11.2",
                help_text="Maps to the Track Record and Trajectory weights.",
            ),
            Question(
                key="depth_vs_range",
                kind=SCALE_QUESTION,
                prompt="Specialist depth, or generalist range?",
                poles=("Specialist depth", "Generalist range"),
                dimension=DIM_VERIFIED_COMPETENCE,
                source="RPN-PHIL-001 §16 S2",
                help_text=(
                    "Shifts competency weighting inside Verified Competence "
                    "rather than moving the dimension itself."
                ),
            ),
            Question(
                key="credentials_vs_practice",
                kind=SCALE_QUESTION,
                prompt="Credentials, or demonstrated practice?",
                poles=("Credentials", "Demonstrated practice"),
                dimension=None,
                source="RPN-PHIL-001 §16 S2",
                help_text=(
                    "Sets evidence-tier preference inside Verified Competence. "
                    "It changes which evidence counts, not how much the "
                    "dimension weighs."
                ),
            ),
            Question(
                key="stability_vs_velocity",
                kind=SCALE_QUESTION,
                prompt="Stability of prior moves, or velocity and change?",
                poles=("Stability of prior moves", "Velocity and change"),
                dimension=None,
                source="RPN-PHIL-001 §16 S2, §8.4",
                help_text="Sets the tenure reading rules, never a tenure filter.",
            ),
            Question(
                key="training_capacity",
                kind=SCALE_QUESTION,
                prompt="How much internal training capacity do you have?",
                poles=("None", "Substantial"),
                dimension=DIM_VERIFIED_COMPETENCE,
                counter_dimension=DIM_TRAJECTORY,
                source="RPN-PHIL-001 §16 S2, §11.2",
                help_text=(
                    "Limited onboarding capacity raises Verified Competence and "
                    "lowers Trajectory. It is a constraint, not a preference."
                ),
            ),
            Question(
                key="non_traditional_tolerance",
                kind=SCALE_QUESTION,
                prompt="How do you feel about non-traditional backgrounds?",
                poles=("Low tolerance", "High tolerance"),
                dimension=None,
                source="RPN-PHIL-001 §16 S2, §8.9, §10.10",
                help_text=(
                    "Sets the number of exploration slots and the pedigree cap. "
                    "It never lowers anyone's bar."
                ),
            ),
        ),
    ),
    # ── §16 Section 3 / Appendix A3 ─────────────────────────────────────────
    Section(
        key="observable_evidence",
        title='What "good" looks like here, as observable evidence',
        intent=(
            "The hardest section and the one that does the most work. Name five "
            "to eight behaviours your strongest people demonstrably show. We "
            "will reject anything that is a description of a person rather than "
            "something somebody could have watched happen. This is the single "
            "highest-leverage part of the whole intake, because unobservable "
            "criteria are exactly where bias enters."
        ),
        source="RPN-PHIL-001 §16 S3, Appendix A3",
        min_items=5,
        max_items=8,
        item_format="Has [done X] and can [describe or demonstrate Y]",
        examples=(
            EvidenceExample(
                rejected="Ownership mindset.",
                accepted=(
                    "Has taken a project from unclear brief to shipped outcome "
                    "without a defined process being handed to them, and can "
                    "describe the decisions they made when nobody told them "
                    "what to do."
                ),
            ),
            EvidenceExample(
                rejected="Team player.",
                accepted=(
                    "Has worked in a matrixed structure where they had "
                    "responsibility without authority, and can describe how "
                    "they secured commitment from people who did not report to "
                    "them."
                ),
            ),
        ),
        questions=(
            Question(
                key="observable_behaviours",
                kind=EVIDENCE_LIST_QUESTION,
                prompt=(
                    "Name the behaviours your strongest performers demonstrably "
                    "show. Five to eight, one per line."
                ),
                help_text=(
                    'Format: "Has [done X] and can [describe or demonstrate Y]". '
                    'Rejected: "Ownership mindset". '
                    "Accepted: \"Has taken a project from unclear brief to "
                    "shipped outcome without a defined process being handed to "
                    'them, and can describe the decisions they made when nobody '
                    'told them what to do".'
                ),
            ),
        ),
    ),
    # ── §16 Section 4 / Appendix A4 ─────────────────────────────────────────
    Section(
        key="failure_modes",
        title="What fails here",
        intent=(
            "The mirror question, and often more informative than the one "
            "before it. Failure modes become risk probes in the validation "
            "instrument and risk-register items in the dossier."
        ),
        source="RPN-PHIL-001 §16 S4, Appendix A4",
        questions=(
            Question(
                key="failed_hires",
                kind=EVIDENCE_LIST_QUESTION,
                prompt=(
                    "Describe two or three hires who looked strong on paper and "
                    "did not work out. What was the actual failure mode?"
                ),
                help_text="Press for specifics. This produces the risk probes.",
            ),
        ),
    ),
    # ── §16 Section 5 / Appendix A5 ─────────────────────────────────────────
    Section(
        key="non_negotiables",
        title="Non-negotiables and constraints",
        intent=(
            "Anything genuinely binary. Each one is tested against §12.3: it has "
            "to be objective, genuinely non-negotiable for the role, not a proxy "
            "for a protected characteristic, and declared before sourcing."
        ),
        source="RPN-PHIL-001 §16 S5, Appendix A5, §12.3",
        questions=(
            Question(
                key="statutory_requirements",
                kind=TEXT_QUESTION,
                prompt="Statutory and policy requirements: background verification, licensure, anything similar?",
                required=False,
            ),
            Question(
                key="notice_tolerance",
                kind=TEXT_QUESTION,
                prompt="What notice period can you tolerate?",
                required=False,
            ),
            Question(
                key="compensation_bands",
                kind=TEXT_QUESTION,
                prompt="Compensation bands by level, and how much flexibility is in them?",
                required=False,
                help_text=(
                    "Used in the risk register, never in scoring. Salary "
                    "history is never collected or used as a ranking input."
                ),
            ),
            Question(
                key="location_rules",
                kind=TEXT_QUESTION,
                prompt="Location, relocation and work-model rules?",
                required=False,
            ),
            Question(
                key="hard_disqualifiers",
                kind=TEXT_QUESTION,
                prompt=(
                    "Is there anything genuinely binary that rules a candidate "
                    "out no matter how strong they are otherwise? One per line."
                ),
                required=False,
                help_text=(
                    "Job-related and lawful only. A required licence, legal work "
                    "authorisation or a statutory qualification is fine. "
                    "Anything about who someone is, rather than what they can "
                    "do, will be refused."
                ),
            ),
        ),
    ),
    # ── §16 Section 6 / Appendix A6 ─────────────────────────────────────────
    Section(
        key="process_shape",
        title="Process shape",
        intent=(
            "What the report has to answer and who is going to read it. A slow "
            "process changes which candidates remain available, and the dossier "
            "should say so."
        ),
        source="RPN-PHIL-001 §16 S6, Appendix A6",
        questions=(
            Question(
                key="interview_stages",
                kind=TEXT_QUESTION,
                prompt="How many interview stages do you actually run, and of what kind?",
            ),
            Question(
                key="decider",
                kind=CHOICE_QUESTION,
                prompt="Who makes the final decision?",
                options=(
                    "The hiring manager alone",
                    "The hiring manager with HR",
                    "A panel",
                    "A founder or executive signs off",
                ),
            ),
            Question(
                key="client_run_assessments",
                kind=TEXT_QUESTION,
                prompt="Do you run your own assessments? Which ones?",
                required=False,
                help_text="So we do not duplicate them.",
            ),
            Question(
                key="turnaround_commitments",
                kind=TEXT_QUESTION,
                prompt="What turnaround can you commit to at each stage?",
                required=False,
            ),
        ),
    ),
    # ── §16 Section 7 / Appendix A7 ─────────────────────────────────────────
    Section(
        key="diversity_commitments",
        title="Diversity and inclusion commitments",
        intent=(
            "Captured as PROCESS commitments, never as quotas applied to "
            "scoring. A slate goal changes who we go looking for; it never "
            "changes how anybody is graded."
        ),
        source="RPN-PHIL-001 §16 S7, Appendix A7",
        questions=(
            Question(
                key="slate_goals",
                kind=TEXT_QUESTION,
                prompt="Do you have slate composition goals at the sourcing stage?",
                required=False,
            ),
            Question(
                key="adverse_impact_reporting",
                kind=CHOICE_QUESTION,
                prompt="Do you want adverse-impact reporting?",
                options=("Yes", "No"),
                required=False,
            ),
            Question(
                key="prohibited_filters_confirmed",
                kind=CHOICE_QUESTION,
                prompt=(
                    "We have shown you the list of filters we will not apply "
                    "under any circumstances. Do you confirm it?"
                ),
                options=("Confirmed", "Not confirmed"),
                help_text=(
                    "Explicit confirmation of the prohibited-disqualifier list. "
                    "It is not a formality: it is what makes a later request to "
                    "add one a conversation rather than a surprise."
                ),
            ),
        ),
    ),
    # ── §16 Section 8 / Appendix A8 ─────────────────────────────────────────
    Section(
        key="data_and_consent",
        title="Data, consent and privacy",
        intent="What candidate data you may receive and hold, and for how long.",
        source="RPN-PHIL-001 §16 S8, Appendix A8",
        questions=(
            Question(
                key="data_receivable",
                kind=TEXT_QUESTION,
                prompt="What candidate data may you receive and retain?",
            ),
            Question(
                key="consent_language",
                kind=TEXT_QUESTION,
                prompt="Is there consent language you require?",
                required=False,
            ),
            Question(
                key="retention_period",
                kind=TEXT_QUESTION,
                prompt="What retention period and deletion obligations apply?",
            ),
            Question(
                key="cross_border",
                kind=TEXT_QUESTION,
                prompt="Any cross-border transfer constraints?",
                required=False,
            ),
            Question(
                key="reference_timing",
                kind=CHOICE_QUESTION,
                prompt="May references be contacted before an offer, or only after?",
                options=("Before an offer is permitted", "Only after an offer"),
            ),
        ),
    ),
    # ── §16 Section 9 / Appendix A9 ─────────────────────────────────────────
    Section(
        key="offer_reality",
        title="Compensation and offer reality",
        intent=(
            "NOT USED IN SCORING. Used in the risk register: a candidate whose "
            "market value exceeds your band is a counter-offer risk you should "
            "know about before you spend interview time on them."
        ),
        source="RPN-PHIL-001 §16 S9, Appendix A9",
        questions=(
            Question(
                key="band_vs_market",
                kind=TEXT_QUESTION,
                prompt="Where does your band sit against the market, by level?",
                required=False,
            ),
            Question(
                key="counter_offer_patterns",
                kind=TEXT_QUESTION,
                prompt="What counter-offer patterns have you seen?",
                required=False,
            ),
        ),
    ),
    # ── §16 Section 10 / Appendix A10 ───────────────────────────────────────
    Section(
        key="sourcing_preferences",
        title="Sourcing preferences",
        intent=(
            "Where to look. Explicitly separated from how to score, and applied "
            "at sourcing only."
        ),
        source="RPN-PHIL-001 §16 S10, Appendix A10, §8.9",
        questions=(
            Question(
                key="target_industries",
                kind=TEXT_QUESTION,
                prompt="Target industries or companies?",
                required=False,
            ),
            Question(
                key="talent_pools",
                kind=TEXT_QUESTION,
                prompt="Any talent pools to prioritise?",
                required=False,
            ),
        ),
    ),
    # ── §16 Section 11 / Appendix A11 ───────────────────────────────────────
    Section(
        key="dossier_preferences",
        title="Dossier presentation preferences",
        intent="Changes emphasis, depth and format only. Never what is assessed.",
        source="RPN-PHIL-001 §16 S11, Appendix A11",
        questions=(
            Question(
                key="dossier_depth",
                kind=CHOICE_QUESTION,
                prompt="How much do you want to read?",
                options=(
                    "The short version",
                    "The standard dossier",
                    "Everything, including the raw evidence",
                ),
            ),
            Question(
                key="first_pass_anonymised",
                kind=CHOICE_QUESTION,
                prompt="Do you want the first pass anonymised?",
                options=("Anonymised first pass", "Named throughout"),
            ),
            Question(
                key="language",
                kind=TEXT_QUESTION,
                prompt="What language should the dossier be written in?",
                required=False,
            ),
        ),
    ),
    # ── §16 Section 12 / Appendix A12 ───────────────────────────────────────
    Section(
        key="historical_calibration",
        title="Historical calibration data",
        intent=(
            "The highest-value input in the entire intake, and worth pursuing "
            "hard. Past hires, who succeeded, who did not, and on what "
            "dimension the failure occurred. It converts our weight baselines "
            "from professional judgement into evidence about YOUR company."
        ),
        source="RPN-PHIL-001 §16 S12, Appendix A12",
        questions=(
            Question(
                key="past_hire_outcomes",
                kind=TEXT_QUESTION,
                prompt=(
                    "Can you supply past hires with their outcomes, and where "
                    "the ones that failed went wrong?"
                ),
                required=False,
            ),
        ),
    ),
)

SECTION_KEYS: tuple[str, ...] = tuple(section.key for section in SECTIONS)

_QUESTIONS: dict[str, Question] = {
    question.key: question
    for section in SECTIONS
    for question in section.questions
}


def question(key: str) -> Question | None:
    return _QUESTIONS.get(key)


# ── Section 3: rejecting an adjective ────────────────────────────────────────

#: Words that describe a PERSON rather than an EVENT. Not a blocklist of bad
#: words -- several are perfectly good English -- but a detector for the shape
#: of answer the instrument exists to refuse.
_TRAIT_WORDS: frozenset[str] = frozenset(
    {
        "mindset", "attitude", "mentality", "personality", "character",
        "ownership", "proactive", "proactivity", "driven", "hungry", "hunger",
        "passionate", "passion", "motivated", "self-starter", "selfstarter",
        "go-getter", "team player", "culture fit", "culturally", "dynamic",
        "energetic", "enthusiastic", "committed", "dedication", "dedicated",
        "hardworking", "hard-working", "smart", "intelligent", "bright",
        "detail-oriented", "detail oriented", "meticulous", "reliable",
        "dependable", "flexible", "adaptable", "resilient", "resilience",
        "leadership qualities", "strong communicator", "excellent communicator",
        "good communicator", "problem solver", "problem-solver", "rockstar",
        "ninja", "guru", "10x",
    }
)

#: Verbs that describe something that HAPPENED. An answer containing one is
#: describing an event, which is what the instrument is asking for.
_EVENT_VERBS: frozenset[str] = frozenset(
    {
        "shipped", "delivered", "built", "launched", "took", "led", "ran",
        "rewrote", "migrated", "closed", "negotiated", "hired", "fired",
        "reduced", "increased", "moved", "changed", "fixed", "resolved",
        "escalated", "presented", "wrote", "designed", "rebuilt", "recovered",
        "turned", "grew", "cut", "automated", "replaced", "introduced",
        "removed", "convinced", "persuaded", "handed", "finished", "completed",
        "solved", "diagnosed", "found", "caught", "missed", "left", "joined",
        "stopped", "started", "raised", "landed", "saved", "onboarded",
    }
)

_MIN_EVIDENCE_WORDS = 8


def is_observable(answer: str) -> bool:
    """True when an answer describes something somebody could have watched.

    The test is deliberately ASYMMETRIC: an answer containing an event verb
    passes even if it also contains a trait word, because "she took ownership of
    the migration and shipped it in six weeks" is a real answer with a trait
    word in it. An answer with no event verb at all and a trait word in it is
    the shape being refused.

    It also refuses answers that are simply too short to be an account of
    anything, which is the other common form of "ownership mindset" -- three
    words, no verb, nothing to probe.
    """
    text = (answer or "").strip().lower()
    if not text:
        return False
    words = re.findall(r"[a-z][a-z'\-]*", text)
    if len(words) < _MIN_EVIDENCE_WORDS:
        return False
    if any(word in _EVENT_VERBS for word in words):
        return True
    # No event verb. A trait word now decides it, and its absence is not enough
    # on its own -- an answer with neither is prose about nothing.
    return not any(trait in text for trait in _TRAIT_WORDS)


def rejection_message(answer: str) -> str:
    """What Bodha says when it refuses an answer and asks again.

    Names the specific phrase it caught and gives the accepted example
    spec-doc5 quotes verbatim. A rejection that does not show the difference
    teaches nothing, and the client simply rephrases the same adjective.
    """
    text = (answer or "").strip().lower()
    caught = next((trait for trait in sorted(_TRAIT_WORDS) if trait in text), None)
    if caught:
        return (
            f"That is a description of a person rather than something I could "
            f"have watched happen -- \"{caught}\" is the part I mean. Could you "
            f"give me the specific thing they did? For instance, instead of "
            f"\"ownership mindset\": \"has taken a project from an unclear brief "
            f"to a shipped outcome\"."
        )
    return (
        "I need a bit more than that -- something specific enough that I could "
        "picture it happening. For instance, instead of \"ownership mindset\": "
        "\"has taken a project from an unclear brief to a shipped outcome\"."
    )


# ── Disqualifiers ────────────────────────────────────────────────────────────

#: Attributes a disqualifier may never rest on. Refused at capture, not at
#: compilation, so the client is told at the moment they typed it.
#:
#: This is the same list `agents/gates.FORBIDDEN_INFERENCE_FIELDS` holds and it
#: is checked here too rather than only there, for the reason enforcement is
#: always checked at the boundary where the information exists: by the time a
#: gate sees a compiled artifact, the sentence the client typed is gone.
PROHIBITED_DISQUALIFIERS: tuple[str, ...] = (
    "age", "aged", "date of birth", "born in", "years old", "young", "old",
    "gender", "male", "female", "sex", "man", "men", "woman", "women",
    "religion", "religious", "caste", "creed",
    "marital", "married", "single", "unmarried", "pregnant", "pregnancy",
    "children", "kids", "childcare", "maternity", "paternity",
    "nationality", "race", "racial", "ethnic", "ethnicity", "colour", "color",
    "disability", "disabled", "handicap", "handicapped",
    "sexual orientation", "gay", "lesbian", "transgender",
    "mother tongue", "native speaker",
)

#: Age bars written as a NUMBER rather than as a word.
#:
#: These exist because the word list alone missed the most common phrasing by a
#: distance: "nobody over 50" and "no candidates over 45" contain no term from
#: the list above, and both are exactly the unlawful requirement it is there to
#: catch. A client stating an age bar rarely uses the word "age" -- they state
#: the number, which is what makes a purely lexical list a detector that catches
#: the careful phrasings and misses the blunt ones.
_AGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(over|under|above|below|more than|less than|at least|max(?:imum)?|min(?:imum)?)\s+\d{2}\b"),
    re.compile(r"\b\d{2}\s*(\+|plus|and above|and over|or younger|or older|yrs? old|years? old)\b"),
    re.compile(r"\b(no|not|nobody|nobody's|none)\b[^.]{0,30}\b\d{2}\b"),
)


def prohibited_in(text: str) -> list[str]:
    """Every protected-attribute term a disqualifier rests on.

    MATCHED ON WORD BOUNDARIES, not as substrings, and the reason is a defect
    this had on its first version: "Must hold a valid CA licence" was refused
    because "hold" contains "old". A false positive here is not harmless -- it
    tells a client their perfectly lawful professional requirement is
    discriminatory, which destroys their trust in every refusal that follows,
    including the ones that are right.

    Multi-word terms are matched as phrases, single words with `\b` anchors.
    """
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return []
    found: list[str] = []
    for term in PROHIBITED_DISQUALIFIERS:
        if " " in term:
            if term in lowered:
                found.append(term)
        elif re.search(rf"\b{re.escape(term)}\b", lowered):
            found.append(term)
    for pattern in _AGE_PATTERNS:
        if pattern.search(lowered):
            found.append("an age limit")
            break
    return found


# ── The compiled artifact ────────────────────────────────────────────────────


@dataclass
class CompanyDNA:
    """Layer 2's compiled output. What Sutra actually reads.

    Sutra never sees `answers` -- spec-doc5 §A.3 is explicit that it retrieves
    "the client's Company DNA artifact (Layer 2) ... never the client's
    free-text preferences directly". The raw answers are kept on the row for
    audit and for recompilation when the compiler changes, and are deliberately
    absent from this object's consumer projection.
    """

    artifact_version: int = ARTIFACT_VERSION
    #: {dimension: multiplier}, already clamped by `layers.resolve`.
    weight_modifiers: dict[str, float] = field(default_factory=dict)
    #: How many independent sources a claim needs before it reads as evidenced.
    independence_required: int = 1
    #: How old evidence may be before it is discounted, in days. None = no decay.
    evidence_max_age_days: int | None = None
    #: Multiplier on the platform threshold for a dimension to pass.
    threshold_modifier: float = 1.0
    #: Lawful, job-related only. Every entry survived `prohibited_in`.
    disqualifiers: list[str] = field(default_factory=list)
    #: Refused disqualifiers, kept so a support conversation has the evidence.
    refused_disqualifiers: list[str] = field(default_factory=list)
    #: §16 S3's observable-evidence statements, used as retrieval context by
    #: Sutra's stage 2 and by Vaada's question generation. Things to look FOR.
    observable_signals: list[str] = field(default_factory=list)
    #: §16 S4's failure modes, converted to risk probes. Things to look OUT
    #: for. Kept separate from `observable_signals` because they steer
    #: questioning in opposite directions and one bucket loses the difference.
    risk_probes: list[str] = field(default_factory=list)
    #: §8.4's tenure reading rule for this client. A READING, never a filter.
    tenure_reading: str = ""
    #: §10.10's exploration slots, from §16 S2's non-traditional tolerance.
    exploration_slots: int = 0
    #: §16 S5 and S8: compliance and process constraints. Not scoring inputs.
    constraints: dict[str, Any] = field(default_factory=dict)
    #: §16 S7. Explicit confirmation of the prohibited-disqualifier list.
    prohibited_filters_confirmed: bool = False
    #: §15's leftover bucket, LABELLED. Anything that is not one of the six
    #: output kinds is context for the recruiter and never configuration for
    #: the engine, and saying so on the artifact is what stops it drifting into
    #: one.
    recruiter_context: dict[str, Any] = field(default_factory=dict)
    #: Ranking prior only. Never decides who is scored.
    sourcing_hints: dict[str, Any] = field(default_factory=dict)
    #: Emphasis and length only. Never what is assessed.
    dossier_preferences: dict[str, Any] = field(default_factory=dict)
    #: Every `layers.Refusal`, as dicts.
    refusals: list[dict[str, Any]] = field(default_factory=list)
    #: Every `layers.Adjustment`, as dicts. This is what makes a moved weight
    #: traceable to the answer that moved it.
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_version": self.artifact_version,
            "weight_modifiers": {
                k: round(v, 4) for k, v in sorted(self.weight_modifiers.items())
            },
            "independence_required": self.independence_required,
            "evidence_max_age_days": self.evidence_max_age_days,
            "threshold_modifier": round(self.threshold_modifier, 4),
            "disqualifiers": list(self.disqualifiers),
            "refused_disqualifiers": list(self.refused_disqualifiers),
            "observable_signals": list(self.observable_signals),
            "risk_probes": list(self.risk_probes),
            "tenure_reading": self.tenure_reading,
            "exploration_slots": self.exploration_slots,
            "constraints": dict(self.constraints),
            "prohibited_filters_confirmed": self.prohibited_filters_confirmed,
            "recruiter_context": dict(self.recruiter_context),
            "sourcing_hints": dict(self.sourcing_hints),
            "dossier_preferences": dict(self.dossier_preferences),
            "refusals": list(self.refusals),
            "provenance": list(self.provenance),
        }


def _scale(value: Any) -> int:
    """Clamp a scale answer into §16's 1..5 range.

    An out-of-range value is a client bug, not a licence to move a weight
    further than the scale allows. It clamps rather than raising because a
    single malformed field must not discard an otherwise complete intake, and
    the clamp is visible in the provenance either way.
    """
    try:
        return max(SCALE_MIN, min(SCALE_MAX, int(value)))
    except (TypeError, ValueError):
        return SCALE_NEUTRAL


#: How far one scale step moves a dimension weight.
#:
#: SOURCE: RPN-PHIL-001 §11.2 with §11.5 (v1.3): §11.2 states each Layer 2 modifier as an ADDITIVE
#: delta with a bound ("D2 up, D5 down, +-0.06"; "D5 up, D2 down, +-0.08"), and
#: this module composes MULTIPLICATIVELY through `layers.resolve`. The Runbook
#: gives no per-step multiplier, and there is no conversion between the two
#: without fixing a baseline. Recorded in RUNBOOK_OPEN_QUESTIONS_PHASE0B.md
#: alongside the same question at §11.3.
#:
#: Small on purpose: six scale answers compose, and a per-step figure large
#: enough to feel decisive on its own produces a compound modifier that would be
#: clamped away, which is the same as not having asked.
_SCALE_STEP = 0.06


#: §7.4, minimum independent groups on must-have competencies by seniority.
#:
#: CORRECTED. The first implementation asked the client how much corroboration
#: they wanted and let the answer set the independence requirement, which
#: inverted the layering: §7.4 is a LAYER 1 table indexed by seniority, and a
#: client answering "a convincing account is enough" was lowering a platform
#: floor through an intake question. C2 makes it a commitment rather than a
#: preference: "no candidate is placed in a delivered shortlist without
#: corroboration across the minimum number of independent sources defined for
#: that department and seniority".
#:
#: A client may still ask for MORE, which is what §11.2's evidence bar is for,
#: and `layers.BOUNDS["evidence_threshold"]` is asymmetric for exactly this
#: reason. The floor itself is not theirs to move.
_MINIMUM_INDEPENDENT_GROUPS: dict[str, int] = {
    "non_managerial": 2,
    "managerial": 3,
    "leadership": 3,
    "cxo": 4,
}


def minimum_independent_groups(seniority: str) -> int:
    """§7.4's floor for this seniority. Raises for an unknown one.

    No default: a seniority nobody mapped must not silently receive the lowest
    corroboration requirement in the table, which is what a `.get(x, 2)` would
    do and is the direction that discloses more and restricts less.
    """
    try:
        return _MINIMUM_INDEPENDENT_GROUPS[seniority]
    except KeyError as exc:
        raise ValueError(
            f"No §7.4 minimum-source standard for seniority {seniority!r}; "
            f"expected one of {tuple(_MINIMUM_INDEPENDENT_GROUPS)}"
        ) from exc


def compile_artifact(
    answers: Mapping[str, Any], *, seniority: str = "non_managerial"
) -> CompanyDNA:
    """Turn a completed intake into the artifact every job for this client uses.

    Deterministic, model-free, and diffable between versions. See the module
    docstring for why that is not merely a convenience.

    Implements §17.1's compilation table: the six output kinds §15 permits, and
    nothing else. "If a client statement cannot be expressed as a weight
    modifier, an evidence requirement, a threshold, a disqualifier, a sourcing
    instruction, or a dossier presentation preference, it is context for the
    recruiter, not configuration for the engine, and it is labelled as such."
    """
    dna = CompanyDNA()

    # ── Weight modifiers, from §16 S2's six forced scales ───────────────────
    #
    # §11.2 pairs most of these: "we hire for potential and train" is D5 up AND
    # D2 down, not D5 up alone. A question that recorded only the lifted half
    # would let successive intakes drift the vector in one direction, because
    # every client would look like they wanted more of something and less of
    # nothing.
    requested: dict[str, float] = {}
    for key, raw in answers.items():
        q = _QUESTIONS.get(key)
        if q is None or q.kind != SCALE_QUESTION:
            continue
        if not q.dimension and not q.counter_dimension:
            # §16 S2 maps two of its six scales to evidence-tier preference and
            # to tenure reading rather than to a weight. Recorded, not weighted.
            continue
        step = _scale(raw) - SCALE_NEUTRAL
        if step == 0:
            # A genuine "no preference". Recorded as answered, modifies nothing.
            continue
        # The scale's HIGH end de-emphasises `dimension` and emphasises
        # `counter_dimension`, which is how §16 S2 prints every one of them:
        # position 5 on "Proven delivery ... Potential" is less track record and
        # more trajectory.
        if q.dimension:
            requested[q.dimension] = requested.get(q.dimension, 1.0) * (
                1.0 - step * _SCALE_STEP
            )
        if q.counter_dimension:
            requested[q.counter_dimension] = requested.get(
                q.counter_dimension, 1.0
            ) * (1.0 + step * _SCALE_STEP)

    # §11.2's last row: "we are regulated and audit-sensitive" raises D4. It is
    # the only Layer 2 modifier the Runbook takes from Section 1 rather than
    # from Section 2, and it is the one a client never thinks to ask for.
    if _is_regulated(answers.get("industry_regulatory")):
        requested[DIM_AUTHENTICITY] = requested.get(DIM_AUTHENTICITY, 1.0) * (
            1.0 + _SCALE_STEP
        )

    resolution = layers.resolve("competency_weight", company=requested)
    dna.weight_modifiers = dict(resolution.multipliers)
    dna.provenance = [a.as_dict() for a in resolution.adjustments]
    dna.refusals = [r.as_dict() for r in resolution.refusals]

    # ── Evidence requirements (§7.4, a Layer 1 floor) ───────────────────────
    dna.independence_required = minimum_independent_groups(seniority)

    # ── Thresholds ──────────────────────────────────────────────────────────
    #
    # Driven by §16 S2's credentials-versus-practice scale, which §16 maps to
    # "evidence tier preferences within D1". A client leaning to demonstrated
    # practice is asking for a HIGHER evidence bar, not a lower one, so the
    # asymmetric bound in `layers.BOUNDS` is doing real work here.
    practice = _scale(answers.get("credentials_vs_practice", SCALE_NEUTRAL))
    requested_threshold = 1.0 + (practice - SCALE_NEUTRAL) * _SCALE_STEP
    threshold_resolution = layers.resolve(
        "evidence_threshold", company={"overall": requested_threshold}
    )
    dna.threshold_modifier = threshold_resolution.multiplier_for("overall")
    dna.provenance.extend(a.as_dict() for a in threshold_resolution.adjustments)
    dna.refusals.extend(r.as_dict() for r in threshold_resolution.refusals)

    # ── Tenure reading (§8.4), never a tenure filter ────────────────────────
    dna.tenure_reading = _TENURE_READING[
        _scale(answers.get("stability_vs_velocity", SCALE_NEUTRAL))
    ]

    # ── Exploration slots and the pedigree cap (§8.9, §10.10) ───────────────
    tolerance = _scale(answers.get("non_traditional_tolerance", SCALE_NEUTRAL))
    dna.exploration_slots = max(0, tolerance - 1)

    # ── Disqualifiers ───────────────────────────────────────────────────────
    for line in str(answers.get("hard_disqualifiers") or "").splitlines():
        entry = line.strip(" -\t")
        if not entry:
            continue
        offending = prohibited_in(entry)
        if offending:
            rule = layers.precedence_rule("role_disqualifier_is_a_protected_proxy")
            dna.refused_disqualifiers.append(entry)
            dna.refusals.append(
                {
                    "key": "disqualifier",
                    "layer": layers.LAYER_COMPANY,
                    "reason": (
                        "Rests on a protected attribute "
                        f"({', '.join(sorted(offending))}). A disqualifier must "
                        "be lawful and job-related (RPN-PHIL-001 §12.3, §12.4)."
                    ),
                    "rule": rule.key,
                    "escalate_to": rule.escalate_to,
                    "alternative": rule.alternative,
                }
            )
            continue
        dna.disqualifiers.append(entry)

    # ── Observable signals (§16 S3) ─────────────────────────────────────────
    # ONLY answers that passed `is_observable` reach here. An adjective that
    # slipped through capture must not become retrieval context for question
    # generation, because a question generated from "ownership mindset" is a
    # question nobody can answer with evidence.
    dna.observable_signals = [
        line for line in _lines(answers.get("observable_behaviours")) if is_observable(line)
    ]

    # ── Risk probes (§16 S4) ────────────────────────────────────────────────
    # §16 is explicit that failure modes "convert into risk probes in the
    # validation instrument and into risk-register items in the dossier". They
    # were compiled into the same undifferentiated `observable_signals` bucket
    # before, which lost the distinction: a signal is something to look FOR and
    # a risk probe is something to look OUT for, and they steer questioning in
    # opposite directions.
    dna.risk_probes = [
        line for line in _lines(answers.get("failed_hires")) if is_observable(line)
    ]

    # ── Sourcing: a ranking prior, never a filter ───────────────────────────
    dna.sourcing_hints = {
        "target_industries": answers.get("target_industries"),
        "talent_pools": answers.get("talent_pools"),
        "slate_goals": answers.get("slate_goals"),
        # Stated on the artifact rather than assumed by the reader, because a
        # hint that could quietly become a filter is exactly how a preference
        # for "companies of a similar size" turns into a candidate never seeing
        # a job.
        "note": "Ranking prior only. Never decides who is assessed.",
    }

    # ── Dossier preferences: emphasis, depth and format only ────────────────
    dna.dossier_preferences = {
        "depth": answers.get("dossier_depth"),
        "first_pass": answers.get("first_pass_anonymised"),
        "language": answers.get("language"),
        "decider": answers.get("decider"),
        "note": "Changes emphasis and length. Never what is assessed.",
    }

    # ── Context for the recruiter, explicitly NOT engine configuration ──────
    #
    # §15's compilation rule: anything that cannot be expressed as one of the
    # six output kinds "is context for the recruiter, not configuration for the
    # engine, and it is labelled as such". Labelling it is the whole point --
    # an unlabelled leftover is how compensation reality ends up influencing a
    # score that §16 S9 says it must never touch.
    dna.recruiter_context = {
        "band_vs_market": answers.get("band_vs_market"),
        "counter_offer_patterns": answers.get("counter_offer_patterns"),
        "interview_capacity": answers.get("interview_capacity"),
        "time_to_hire": answers.get("time_to_hire"),
        "turnaround_commitments": answers.get("turnaround_commitments"),
        "note": (
            "Risk register and recruiter context. Never a scoring input, and "
            "compensation is never a ranking input (RPN-PHIL-001 §12.4)."
        ),
    }

    # ── Compliance constraints (§16 S5, S8) ─────────────────────────────────
    dna.constraints = {
        "statutory_requirements": answers.get("statutory_requirements"),
        "notice_tolerance": answers.get("notice_tolerance"),
        "location_rules": answers.get("location_rules"),
        "data_receivable": answers.get("data_receivable"),
        "retention_period": answers.get("retention_period"),
        "cross_border": answers.get("cross_border"),
        "reference_timing": answers.get("reference_timing"),
    }
    dna.prohibited_filters_confirmed = (
        str(answers.get("prohibited_filters_confirmed") or "") == "Confirmed"
    )
    return dna


#: §8.4's tenure reading rules, indexed by §16 S2's stability-versus-velocity
#: answer. A READING, never a filter: §12.4 prohibits employment gaps of any
#: length as a disqualifier, and short tenure is not a gap in any case.
_TENURE_READING: dict[int, str] = {
    1: "Long tenures read as commitment; short ones need an explanation.",
    2: "Long tenures read favourably; short ones are worth a question.",
    3: "Tenure is read as context, and neither direction is a signal on its own.",
    4: "Frequent moves read as range; long tenures are worth a question about scope.",
    5: "Frequent moves read as range and appetite for change.",
}

#: Words that put a client inside §11.2's "regulated and audit-sensitive" row.
#: Deliberately short and specific: over-matching here raises the authenticity
#: floor for a client who never asked, which costs candidates evidence they
#: cannot supply.
_REGULATED_MARKERS: tuple[str, ...] = (
    "regulated", "regulatory", "audit", "compliance", "banking", "bank",
    "insurance", "pharma", "pharmaceutical", "medical device", "healthcare",
    "aviation", "defence", "defense", "nuclear", "financial services",
    "sebi", "rbi", "irdai", "fda", "hipaa", "pci",
)


#: Words after which the next few words are being DENIED, not asserted.
_NEGATIONS: tuple[str, ...] = ("no", "not", "none", "without", "zero", "nil", "minimal")

#: How many words a negation reaches over. Three covers "no regulatory
#: exposure" and "not an audit sensitive business" without swallowing the rest
#: of a sentence, which would let one "no" at the start of an answer suppress a
#: genuine marker later in it.
_NEGATION_SPAN = 3


def _is_regulated(answer: Any) -> bool:
    """Does this client sit inside §11.2's "regulated and audit-sensitive" row?

    NEGATED SPANS ARE STRIPPED BEFORE MATCHING, and that is not defensive
    tidiness: "no regulatory exposure" contains "regulatory", and a plain
    substring test reads it as regulated. This codebase has already paid for
    that exact class of defect once, when a disqualifier check refused "must
    hold a valid CA licence" because "hold" contains "old".

    The cost here is different from that one and worth stating. A false
    positive raises the authenticity floor for a client who never asked for it,
    which asks candidates for corroboration the role does not need. The
    direction is safe for the client and expensive for the candidate, which is
    exactly the kind of asymmetry that goes unnoticed, because nobody complains
    on behalf of the candidate who quietly graded lower.
    """
    words = str(answer or "").lower().replace(",", " ").split()
    kept: list[str] = []
    skip = 0
    for word in words:
        stripped = word.strip(".;:!?")
        if skip > 0:
            skip -= 1
            continue
        if stripped in _NEGATIONS:
            skip = _NEGATION_SPAN
            continue
        kept.append(stripped)
    text = " ".join(kept)
    return any(marker in text for marker in _REGULATED_MARKERS)


def _lines(answer: Any) -> list[str]:
    """One list item per line, blank lines dropped.

    §16 S3 and S4 both collect several items in one field, and Appendix A3
    prints them as numbered lines. Splitting here rather than at each call site
    means one definition of what an item is.
    """
    return [
        line.strip(" -\t")
        for line in str(answer or "").splitlines()
        if line.strip(" -\t")
    ]


# ── Session progress ─────────────────────────────────────────────────────────


def required_keys() -> tuple[str, ...]:
    return tuple(q.key for q in _QUESTIONS.values() if q.required)


def next_unanswered(answers: Mapping[str, Any]) -> Question | None:
    """The next question Bodha should ask, in instrument order.

    Returns None when every required question is answered, which is what closes
    the session. Order is the SECTIONS order and not a model's choice: the
    instrument builds context section by section, and a model free to reorder
    would ask about failure modes before it knew what the company does.
    """
    for section in SECTIONS:
        for q in section.questions:
            if not q.required:
                continue
            value = answers.get(q.key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return q
    return None


def completeness(answers: Mapping[str, Any]) -> dict[str, Any]:
    required = required_keys()
    answered = [
        key
        for key in required
        if answers.get(key) is not None
        and not (isinstance(answers.get(key), str) and not str(answers[key]).strip())
    ]
    missing = [key for key in required if key not in answered]
    return {
        "required": len(required),
        "answered": len(answered),
        "missing": missing,
        "complete": not missing,
    }

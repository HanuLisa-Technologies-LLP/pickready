"""Siddhi's remarks: the prose beside every grade, and the rules it cannot break.

Moved here from `functional_assessment` in the Vivekium release, so that
"Siddhi writes the prose, Miti writes no prose" is literally true of the code
(PLAN-p5 section 3.3). The rules are unchanged; what changed is the RETURN.

A REMARK SAYS HOW IT WAS WRITTEN
----------------------------------
`bounded_remark` used to return a bare string, so a remark a model wrote and
the fixed template the loop fell back to during an outage were
indistinguishable to every caller, and both went into a report a client reads
as though somebody had assessed the evidence. That is template output presented
as generation, which rule 6 forbids. It now returns a `Remark(text, source)`:

  * `model`      a model wrote it and the deterministic critic accepted it;
  * `template`   the model was unavailable or never satisfied the critic, and
                 this is the fixed fallback. Recorded to provenance, flags the
                 report for human review, and the reader is told in words
                 (`TEMPLATE_REMARK_NOTE`);
  * `catalogue`  a fixed factual sentence written on purpose where no model
                 should run: a skill the candidate never answered, or one whose
                 evaluation could not be completed. Not a fallback, so it does
                 not claim to be one, and it names what happened.

REMARKS ARE GENERATED COMPLETE INSIDE THEIR WORD CONTRACT AND NEVER TRUNCATED
------------------------------------------------------------------------------
Out-of-range output is regenerated in full (CLAUDE.md hard rule). Every rated
skill and the Overall remark are 45 to 50 words. The 25 to 30 word contract
belonged to the AI Score's per-category remarks, which Phase 2 retires, and the
fallback written for it is not carried here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services import agent_loop, llm_router
from app.services.siddhi import numbers
from app.services.siddhi.provenance import ProvenanceSink

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_MODEL",
    "SOURCE_TEMPLATE",
    "SOURCE_CATALOGUE",
    "REMARK_SOURCES",
    "SKILL_REMARK_WORDS",
    "OVERALL_REMARK_WORDS",
    "PROBE_REMARK_WORDS",
    "REPORT_BANNED_PHRASES",
    "TEMPLATE_REMARK_NOTE",
    "NOT_ASSESSED_REMARK",
    "remark_note",
    "Remark",
    "word_count",
    "invented_terms",
    "evidence_anchor",
    "template_remark",
    "rating_differentiated_template",
    "unanswered_remark",
    "not_assessed_remark",
    "bounded_remark",
    "REMARK_TASK_TYPE",
]

SOURCE_MODEL = "model"
SOURCE_TEMPLATE = "template"
SOURCE_CATALOGUE = "catalogue"
REMARK_SOURCES: frozenset[str] = frozenset(
    {SOURCE_MODEL, SOURCE_TEMPLATE, SOURCE_CATALOGUE}
)

#: Every rated skill's remark and the Overall remark (spec section 9.5).
SKILL_REMARK_WORDS = (45, 50)
OVERALL_REMARK_WORDS = SKILL_REMARK_WORDS
#: A gap probe is a prompt for the interviewer, not a written assessment, and
#: is capped shorter than a remark for exactly that reason.
PROBE_REMARK_WORDS = (25, 30)

#: The one task type every remark is written on. `report_synthesis` is the
#: judge-tier task: a remark states what the grade rests on.
REMARK_TASK_TYPE = "report_synthesis"

REPORT_BANNED_PHRASES: tuple[str, ...] = (
    "produced usable evidence for",
    "credible but not exhaustive",
    "approaches this work in practice",
    "describe one recent situation in detail",
)

#: What a reader is told beside a remark that a template wrote. Words only, no
#: em dash, and it says what happened rather than apologising for it.
TEMPLATE_REMARK_NOTE = (
    "Written from a fixed template because the writing model was unavailable."
)

#: The catalogue sentence for a skill whose evaluation could not be completed
#: (a model outage on every substantive answer, PLAN-p5 P5-D3). 45 to 50 words,
#: no model call, and it says the one true thing: nothing was concluded, and a
#: person should read the answers.
NOT_ASSESSED_REMARK = (
    "This skill could not be evaluated because the evaluation service did not "
    "complete for the answers recorded against it. No grade is stated, and "
    "nothing about the candidate should be inferred from its absence. A person "
    "should read the candidate's own answers in the transcript and judge this "
    "skill directly."
)


def remark_note(remark_provenance: str | None) -> str | None:
    """The words-only marker beside a stored remark (`DimensionOut.remark_note`).

    Only a TEMPLATE says so. A model remark needs no marker, a catalogue
    sentence says what happened in its own words, and a row written before
    remark provenance existed (None) is not guessed at.
    """
    return TEMPLATE_REMARK_NOTE if remark_provenance == SOURCE_TEMPLATE else None


@dataclass(frozen=True)
class Remark:
    """A remark and how it was written. See the module docstring."""

    text: str
    source: str

    def __post_init__(self) -> None:
        if self.source not in REMARK_SOURCES:
            raise ValueError(
                f"Unknown remark source {self.source!r}. How a remark was "
                f"written is a closed vocabulary: {sorted(REMARK_SOURCES)}."
            )

    @property
    def is_template(self) -> bool:
        return self.source == SOURCE_TEMPLATE

    def row_fields(self) -> dict[str, Any]:
        """The two fields a report dimension row carries for this remark."""
        return {"remark": self.text, "remark_provenance": self.source}


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w&'-]+\b", value or ""))


# ── Templates ────────────────────────────────────────────────────────────────


def template_remark(name: str) -> str:
    """The 45 to 50 word fallback when no rating is known.

    Two candidates, the second dropping the item name: a long competency name
    ("Stakeholder & board management") pushes the first variant over the
    ceiling, and the contract is a COMPLETE remark inside the range, never a
    truncated one.
    """
    candidates = [
        (
            f"The available answer record links {name} to actions the candidate described and outcomes they reported. "
            "One conversation cannot establish consistency across situations, so an interviewer should request another "
            "example, examine the decision trade-offs, and confirm which result the candidate personally owned from "
            "start to finish."
        ),
        (
            "The available answer record links this area to actions the candidate described and outcomes they reported. "
            "One conversation cannot establish consistency across situations, so an interviewer should request another "
            "example, examine the decision trade-offs, and confirm which result the candidate personally owned from "
            "start to finish."
        ),
    ]
    return next(value for value in candidates if 45 <= word_count(value) <= 50)


_ANCHOR_IGNORED: frozenset[str] = frozenset(
    {
        "answer", "answers", "candidate", "candidates", "candidate's",
        "evidence", "item", "question", "questions", "skill", "their", "this",
        "the", "own", "each", "graded", "against", "rubric", "written", "for",
        "that", "produced", "it", "probing", "competency",
    }
)


def evidence_anchor(evidence: str) -> str:
    """A short, candidate-specific phrase safe to embed in a template."""
    words = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+'-]*", evidence or "")
        if token.casefold() not in _ANCHOR_IGNORED
    ]
    return " ".join(words[:4]) or "the available account"


_RATING_TEMPLATES: dict[str, str] = {
    "Highly Matching": (
        "The candidate tied {anchor} to a specific situation, explained the action personally taken, and identified the resulting outcome. "
        "That detail demonstrates highly matching capability in this area. Interview verification should now test whether the same judgement "
        "and depth remain consistent when constraints, scale, or stakeholders change."
    ),
    "Matching": (
        "The candidate connected {anchor} to relevant work, with enough detail to confirm matching capability and a credible personal contribution. "
        "The account leaves one useful verification area: interviewers should probe the hardest trade-off, how the result was checked, and whether "
        "the candidate could repeat the approach independently."
    ),
    "Moderately Matching": (
        "The answer referred to {anchor}, but only partly connected the situation to a personal decision, precise action, or verified outcome. "
        "This is incomplete evidence. Interviewers should probe the gap directly, asking what the candidate personally changed, how they measured "
        "the result, and what they learned."
    ),
    "Not Matching": (
        "The conversation did not establish capability beyond {anchor}; it contained no sufficiently clear owned action, technical reasoning, or outcome "
        "for this area. Interviewers should treat the criterion as unresolved and use a direct role-specific probe to distinguish missing knowledge "
        "from capability the candidate simply did not express."
    ),
}


def rating_differentiated_template(evidence: str, rating: str | None) -> str:
    """The 45 to 50 word template for a known grade, anchored on the evidence."""
    template = _RATING_TEMPLATES.get(str(rating or ""))
    if template is None:
        return template_remark("this area")
    value = template.format(anchor=evidence_anchor(evidence))
    if 45 <= word_count(value) <= 50:
        return value
    # An unusual anchor cannot usually move these templates out of range; this
    # keeps the contract if one does, with a sentence that is still complete.
    return template_remark("this area")


def unanswered_remark(name: str) -> Remark:
    """The catalogue sentence for a skill the candidate gave no substantive answer on."""
    candidates = [
        (
            f"No substantive answer addressed {name} during the completed assessment conversation. The candidate did not describe a situation that "
            "shows this capability, so nothing here can be graded on demonstrated behaviour. An interviewer should treat "
            "it as an open question and probe it directly before drawing a conclusion."
        ),
        (
            "No substantive answer addressed this item during the completed assessment conversation. The candidate did not describe a situation that "
            "shows the capability, so nothing here can be graded on demonstrated behaviour. An interviewer should treat "
            "it as an open question and probe it directly before drawing a conclusion."
        ),
    ]
    text = next(value for value in candidates if 45 <= word_count(value) <= 50)
    return Remark(text=text, source=SOURCE_CATALOGUE)


def not_assessed_remark() -> Remark:
    """The catalogue sentence for a skill whose evaluation could not complete."""
    return Remark(text=NOT_ASSESSED_REMARK, source=SOURCE_CATALOGUE)


#: The Overall Remark when Miti withheld the overall because a Must-have skill
#: was still not assessed on the final attempt (PLAN-p5 P5-D4). 45 to 50 words,
#: no model call: a model asked to summarise a candidate whose essential skill
#: nobody could grade would write a judgement nobody made.
NOT_ASSESSED_OVERALL_REMARK = (
    "The overall suitability could not be stated because at least one "
    "Must-have skill could not be evaluated after repeated attempts. No "
    "overall grade is given, and nothing should be inferred from its absence. "
    "A person should read the candidate's answers for the skills marked not "
    "assessed before deciding."
)


def not_assessed_overall_remark() -> Remark:
    """The catalogue Overall Remark for a withheld overall."""
    return Remark(text=NOT_ASSESSED_OVERALL_REMARK, source=SOURCE_CATALOGUE)


# ── The invented-term guard ──────────────────────────────────────────────────

#: Capitalised words that are ordinary English rather than a named technology,
#: employer or product. Without this the check would flag every sentence that
#: begins with "Interview" or "Evidence", which is most of them.
_ORDINARY_CAPITALISED: frozenset[str] = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "they", "their",
    "he", "she", "his", "her", "it", "its", "we", "our", "you", "your",
    "and", "but", "for", "with", "without", "while", "when", "where", "which",
    "who", "whose", "what", "how", "why", "if", "then", "than", "there",
    "here", "both", "each", "either", "neither", "all", "any", "some", "no",
    "not", "only", "also", "however", "although", "though", "because",
    "candidate", "candidates", "interview", "interviews", "interviewer",
    "evidence", "experience", "answers", "answer", "discussion", "discussions",
    "role", "roles", "work", "team", "teams", "delivery", "design", "designs",
    "further", "strong", "clear", "limited", "little", "more", "most",
    "recent", "recently", "across", "during", "given", "described",
    "demonstrated", "confirmed", "probing", "probe", "probes", "should",
    "would", "could", "may", "can", "will", "must", "one", "two", "three",
    "several", "many", "few", "at", "in", "on", "of", "to", "from", "by",
    "as", "is", "was", "were", "are", "be", "been", "has", "had", "have",
    "do", "does", "did", "so", "such", "under", "over", "into", "about",
})


def invented_terms(value: str, *, evidence: str, name: str) -> list[str]:
    """Proper nouns in a remark that appear NOWHERE in its source.

    The loop already checks the other direction -- a remark must quote at least
    one concrete term from the evidence. That catches a remark that says
    nothing; it does not catch one that says too much. "Demonstrates strong
    Kubernetes experience" for a candidate who never mentioned Kubernetes is
    the failure mode a client would actually notice.

    Deliberately CONSERVATIVE, in the direction that matters. A guard that
    rejects a good remark costs a round of latency every time and, worse,
    teaches the next reader to loosen it. So a token is only reported when all
    of these hold:

      * it is capitalised and NOT at the start of a sentence (a sentence-
        initial capital carries no information about proper-noun-ness);
      * it is not an ordinary English word;
      * it does not appear in the evidence or in the dimension's own name --
        the name comes from the job's skills, so naming the skill being
        assessed is always legitimate;
      * it is not a plain morphological variant of something that does (so
        "Kafka" in the evidence permits "Kafka's").

    Returns the offending tokens, so the rejection fed back to the model can
    name them. It is also the deterministic half of `siddhi.support`: one rule
    for "names something the evidence does not", used by the writer and by the
    checker, so the two cannot disagree.
    """
    haystack = f"{evidence} {name}".casefold()
    known = set(re.findall(r"[a-z0-9+#.]+", haystack))

    invented: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", value or ""):
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", sentence)
        for position, token in enumerate(tokens):
            if position == 0:
                continue
            if not token[:1].isupper():
                continue
            folded = token.casefold().strip(".")
            if folded in _ORDINARY_CAPITALISED or len(folded) < 3:
                continue
            stem = folded.rstrip("s").rstrip("'")
            if folded in known or stem in known:
                continue
            if any(stem and stem in candidate for candidate in known):
                continue
            invented.append(token)
    return sorted(set(invented))


# ── The writer ───────────────────────────────────────────────────────────────


async def bounded_remark(
    session: Any,
    name: str,
    evidence: str,
    minimum: int = SKILL_REMARK_WORDS[0],
    maximum: int = SKILL_REMARK_WORDS[1],
    *,
    rating: str | None,
    provenance: ProvenanceSink,
) -> Remark:
    """A COMPLETE remark inside the word contract, never truncated.

    Runs through `agent_loop` with deterministic criteria: the word range, the
    delivered-document number rule (`siddhi.numbers.scan_text`, which subsumes
    the conversation guard), the banned phrases, an evidence anchor, and the
    invented-term guard. A rejection is fed back verbatim as an instruction.

    On a degraded loop the template is returned as `source="template"` and the
    template is recorded to `provenance`; the model call is recorded only when
    an attempt was accepted. Nothing here claims a call that did not happen.

    `session` is accepted for call-site symmetry and is not used: the remark
    call is made with `session=None` so the router's own session handling does
    not share the scoring transaction.
    """
    del session
    fallback = rating_differentiated_template(evidence, rating)

    system = (
        f"Write one complete, evidence-based assessment remark of exactly {minimum}-{maximum} words "
        f"for '{name}'. Ground every clause in the specific evidence supplied: quote or paraphrase what "
        "this candidate actually said. Do not use templated phrasing that would fit any candidate, and "
        "do not include a score, percentage, grade, recommendation, or heading. "
        + (
            {
                "Highly Matching": "Cite the specific example, personal action, and outcome. ",
                "Matching": "Confirm the demonstrated evidence and name exactly one useful probe area. ",
                "Moderately Matching": "Diagnose the precise partial gap and name what needs probing. ",
                "Not Matching": "State what was absent and propose a probe that distinguishes missing knowledge from unexpressed capability. ",
            }.get(rating or "", "")
        )
        + f"Evidence: {evidence}"
    )

    async def execute(reflection: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Return only the remark."},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        return (
            await llm_router.chat_completion(
                REMARK_TASK_TYPE, messages, session=None
            )
        ).strip()

    def evaluate(value: str) -> agent_loop.Critique:
        defects: list[agent_loop.Defect] = []
        if not value:
            defects.append(
                agent_loop.Defect(
                    "empty",
                    f"remark.{name}",
                    "return the remark itself, not an empty response",
                )
            )
        words = word_count(value)
        if not (minimum <= words <= maximum):
            defects.append(
                agent_loop.Defect(
                    "length",
                    f"remark.{name}",
                    (
                        f"write between {minimum} and {maximum} words; the previous "
                        f"attempt was {words}"
                    ),
                )
            )
        # THE DELIVERED-REPORT RULE, which is stricter than the conversation's:
        # `scan_text` refuses a bare percentage the interviewer guard permits.
        # Rejected, never redacted: a redacted remark is a sentence with a hole
        # in it, and a rejection is fed back and the sentence written again.
        for violation in numbers.scan_text(value, path=f"remark.{name}"):
            defects.append(
                agent_loop.Defect(
                    "numeric_score",
                    f"remark.{name}",
                    (
                        "a delivered report carries no figure at all, not a "
                        "score, a rating out of a total, a percentile, or a "
                        f"percentage quoted from the candidate: {violation.detail}. "
                        "Describe the evidence in words only."
                    ),
                )
            )
        defects.extend(
            agent_loop.banned_phrase_gate(
                value,
                REPORT_BANNED_PHRASES,
                location=f"remark.{name}",
            ).defects
        )
        evidence_terms = {
            token
            for token in re.findall(r"[a-z0-9]+", evidence.casefold())
            if len(token) >= 5
        }
        output_terms = set(re.findall(r"[a-z0-9]+", value.casefold()))
        if evidence_terms and not evidence_terms.intersection(output_terms):
            defects.append(
                agent_loop.Defect(
                    "evidence_anchor",
                    f"remark.{name}",
                    "quote or paraphrase at least one concrete term from the supplied evidence",
                )
            )
        fabricated = invented_terms(value, evidence=evidence, name=name)
        if fabricated:
            defects.append(
                agent_loop.Defect(
                    "invented_term",
                    f"remark.{name}",
                    (
                        "do not name anything the candidate did not mention: "
                        + ", ".join(fabricated[:3])
                        + ". Write only about what is in the evidence supplied."
                    ),
                )
            )
        return agent_loop.reject_defects(*defects) if defects else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="report_remark",
        execute=execute,
        evaluate=evaluate,
        fallback=fallback,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded:
        provenance.template(f"remark:{name}")
        logger.warning(
            "siddhi.remark_template item=%s attempts=%s error=%s",
            name, result.attempts, result.error,
        )
        return Remark(text=result.value, source=SOURCE_TEMPLATE)
    provenance.model_call(REMARK_TASK_TYPE, None)
    return Remark(text=result.value, source=SOURCE_MODEL)

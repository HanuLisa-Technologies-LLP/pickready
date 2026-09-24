"""Deterministic grounding: every evidence claim must be IN the resume.

Owner requirement (master prompt, Phase 2): "The model returns evidence tags
per component, each tag grounded in resume text. Verify that grounding
deterministically; ungrounded tags are dropped and recorded." Nothing here
calls a model. A model checking a model's quotation would fail exactly when
the provider is already failing and would make the criterion unfalsifiable,
the argument `agent_loop` makes for deterministic success criteria.

WHAT "GROUNDED" MEANS
---------------------
A quote is grounded when, after both sides are normalised the same way
(`normalise`), it is at least `MIN_QUOTE_WORDS` words long and appears in the
resume the model was SHOWN, on word boundaries. The resume text is the
anonymised, compensation-redacted, guarded text (`inputs.prepare_resume`), so a
quote can only ground against words the model was actually given.

THE RULES, IN ORDER (`ground`)
------------------------------
1. A skill claimed `strong` or `some` with a grounded quote is kept.
2. A skill claimed `strong` or `some` whose quote does NOT ground loses the
   quote, which is recorded. If a resume line names the skill (the skill name
   or an ontology equivalent, `skill_term_line`), the verdict becomes `some`
   with that line as its quote (`grounded_by_term`); otherwise `none`.
3. A skill judged `none` while a resume line names it becomes `some` with that
   line, recorded as `contradicted_negative`: the recruiter is never told "no
   Kafka" about a resume that says Kafka. A NEGATIVE tag is emitted only for a
   Must-have skill whose final verdict is `none` AND whose name appears
   nowhere in the resume, not even spread across lines.
4. Experience level, role fit and each company need: a `strong` or `some`
   verdict needs a grounded quote AND a clean tag, or the item is EXCLUDED
   (unknown, never negative) and recorded. `none` needs neither and counts as
   judged. An item naming a need that was never listed is dropped and
   recorded.
5. A skill's tag text is never model-written: the tag stores the skill id and
   the name is resolved from the live row when it is read, so a rename changes
   the label and never the score or the order.

TAG HYGIENE (`clean_tag`)
-------------------------
A model-written tag reaches a recruiter's screen, so it is held to the copy
rules: at most five words and forty characters, no digit (rule 1), no em dash
(rule 7), no culture-fit term, no protected attribute, no grade word or rubric
talk (`conversation_guardrails.inspect_agent_output` must leave it unchanged),
no meta-commentary about evidence (`generation_sufficiency`), and no score
vocabulary. A tag that fails is dropped with its item, and the drop recorded.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.services import conversation_guardrails, generation_sufficiency, ppi
from app.services.assessment_contract import BUCKET_MUST_HAVE, ContractSkill
from app.services.hiring import observable, ontology
from app.services.yukti import config

__all__ = [
    "GroundedItem",
    "GroundedJudgement",
    "GroundedNeed",
    "GroundedSkill",
    "RawItem",
    "RawJudgement",
    "RawNeed",
    "ResumeIndex",
    "clean_tag",
    "ground",
    "is_grounded",
    "normalise",
    "skill_mentioned",
    "skill_term_line",
]

EM_DASH = chr(8212)

#: Quote and dash variants a model (or a PDF extractor) substitutes freely.
_TRANSLATE = str.maketrans(
    {
        chr(0x2018): "'", chr(0x2019): "'", chr(0x201A): "'", chr(0x201B): "'",
        chr(0x201C): '"', chr(0x201D): '"', chr(0x201E): '"', chr(0x201F): '"',
        chr(0x2010): "-", chr(0x2011): "-", chr(0x2012): "-", chr(0x2013): "-",
        chr(0x2014): "-", chr(0x2015): "-", chr(0x2212): "-",
        chr(0x00A0): " ", chr(0x2009): " ", chr(0x202F): " ",
    }
)

#: Everything that is not a letter, a digit, `+` or `#` becomes a separator,
#: so "Node.js", "node js" and "NODE-JS" agree while "C++" and "C#" survive.
_SEPARATORS = re.compile(r"[^\w+#]+|_+")

#: Score vocabulary a tag must never carry, whatever else it says.
_SCORE_WORDS = re.compile(
    r"\b(?:score[sd]?|scoring|percent(?:age)?|percentile|rank(?:ed|ing)?|"
    r"rating|rated|grade[sd]?|points?)\b",
    re.IGNORECASE,
)

#: A tag may carry letters, spaces and a little punctuation, and nothing else:
#: no digit, no emoji, no symbol a renderer would have to interpret.
_TAG_CHARS = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ \-/&+.#,'()])*$")


def normalise(text: str | None) -> str:
    """The comparison form of a resume line or a quote.

    NFKC, quote and dash variants unified, casefolded, every run of separators
    collapsed to one space, trimmed. Applied identically to both sides, so the
    comparison is about words, not about how a PDF extractor spaced them.
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).translate(_TRANSLATE)
    return " ".join(_SEPARATORS.sub(" ", folded.casefold()).split())


@dataclass(frozen=True)
class ResumeIndex:
    """One resume, prepared once for every check against it."""

    text: str
    lines: tuple[str, ...]
    haystack: str  # " " + normalise(text) + " ", for word-boundary search

    @classmethod
    def of(cls, text: str) -> "ResumeIndex":
        lines = tuple(line for line in text.splitlines() if line.strip())
        return cls(text=text, lines=lines, haystack=f" {normalise(text)} ")


def is_grounded(quote: str | None, index: ResumeIndex) -> bool:
    """Is `quote` a verbatim passage of the resume, on word boundaries?"""
    if not quote or len(quote) > config.MAX_QUOTE_CHARS:
        return False
    needle = normalise(quote)
    if len(needle.split()) < config.MIN_QUOTE_WORDS:
        return False
    return f" {needle} " in index.haystack


def skill_term_line(skill_name: str, index: ResumeIndex) -> str | None:
    """The first resume line that names the skill, or None.

    `ontology.matches` is the one "does this text evidence this requirement
    once vocabulary is set aside" function: the skill name or an equivalent at
    token boundaries, or every significant word of it present. Asked of ONE
    LINE at a time here, so the line returned can serve as a quote.
    """
    for line in index.lines:
        if ontology.matches(skill_name, line):
            return line.strip()
    return None


def skill_mentioned(skill_name: str, index: ResumeIndex) -> bool:
    """Does the resume name the skill ANYWHERE, even spread across lines?

    Looser than `skill_term_line` on purpose: it decides only whether a
    negative tag may be shown, and the safe direction for "this candidate has
    no X" is to say it less often.
    """
    return ontology.matches(skill_name, index.text)


def clean_tag(tag: Any) -> str | None:
    """The tag, tidied, if it may reach a recruiter's screen; else None."""
    if not isinstance(tag, str):
        return None
    text = " ".join(tag.split()).strip(" .,;:")
    if not text:
        return None
    if len(text) > config.MAX_TAG_CHARS or len(text.split()) > config.MAX_TAG_WORDS:
        return None
    if EM_DASH in text or any(ch.isdigit() for ch in text):
        return None
    if not _TAG_CHARS.match(text):
        return None
    if _SCORE_WORDS.search(text):
        return None
    if ppi.is_forbidden_competency(text) or observable.prohibited_in(text):
        return None
    if conversation_guardrails.inspect_agent_output(text) != text:
        return None
    if generation_sufficiency.meta_commentary_defects(text):
        return None
    return text


# ── The model's reading, as the judge parsed it ─────────────────────────────


@dataclass(frozen=True)
class RawItem:
    """One verdict the model returned: experience, role fit, or a skill."""

    verdict: str
    quote: str = ""
    tag: str = ""


@dataclass(frozen=True)
class RawNeed:
    need_ref: str
    verdict: str
    quote: str = ""
    tag: str = ""


@dataclass(frozen=True)
class RawJudgement:
    """One candidate's reading, keyed by SKILL ID (refs already resolved)."""

    skills: Mapping[Any, RawItem]
    experience: RawItem
    role_fit: RawItem
    needs: tuple[RawNeed, ...]


# ── The reading after grounding ─────────────────────────────────────────────


@dataclass(frozen=True)
class GroundedSkill:
    skill: ContractSkill
    verdict: str
    quote: str | None
    grounded_by_term: bool = False


@dataclass(frozen=True)
class GroundedItem:
    verdict: str
    quote: str | None
    tag: str | None


@dataclass(frozen=True)
class GroundedNeed:
    need_ref: str
    source: str
    verdict: str
    quote: str | None
    tag: str | None


@dataclass(frozen=True)
class GroundedJudgement:
    """What survives grounding, and a record of everything that did not."""

    skills: tuple[GroundedSkill, ...]
    experience: GroundedItem | None       # None = excluded
    role_fit: GroundedItem | None         # None = excluded
    needs: tuple[GroundedNeed, ...]
    negative_skills: tuple[ContractSkill, ...]
    ungrounded: tuple[Mapping[str, str], ...] = field(default=())
    contradicted_negative: tuple[str, ...] = field(default=())
    tags_refused: tuple[Mapping[str, str], ...] = field(default=())
    unknown_needs: tuple[str, ...] = field(default=())


_CLAIMS = (config.VERDICT_STRONG, config.VERDICT_SOME)


def _ground_skill(
    skill: ContractSkill,
    raw: RawItem | None,
    index: ResumeIndex,
    ungrounded: list[Mapping[str, str]],
    contradicted: list[str],
) -> GroundedSkill:
    verdict = raw.verdict if raw is not None else config.VERDICT_NONE
    quote = raw.quote if raw is not None else ""
    if verdict in _CLAIMS:
        if is_grounded(quote, index):
            return GroundedSkill(skill, verdict, quote.strip())
        ungrounded.append({"kind": config.TAG_KIND_SKILL, "ref": str(skill.id)})
        line = skill_term_line(skill.name, index)
        if line is not None:
            return GroundedSkill(skill, config.VERDICT_SOME, line, grounded_by_term=True)
        return GroundedSkill(skill, config.VERDICT_NONE, None)
    line = skill_term_line(skill.name, index)
    if line is not None:
        contradicted.append(str(skill.id))
        return GroundedSkill(skill, config.VERDICT_SOME, line, grounded_by_term=True)
    return GroundedSkill(skill, config.VERDICT_NONE, None)


def _ground_item(
    kind: str,
    ref: str,
    raw: RawItem,
    index: ResumeIndex,
    ungrounded: list[Mapping[str, str]],
    refused: list[Mapping[str, str]],
) -> GroundedItem | None:
    if raw.verdict == config.VERDICT_NONE:
        return GroundedItem(config.VERDICT_NONE, None, None)
    if not is_grounded(raw.quote, index):
        ungrounded.append({"kind": kind, "ref": ref})
        return None
    tag = clean_tag(raw.tag)
    if tag is None:
        refused.append({"kind": kind, "ref": ref})
        return None
    return GroundedItem(raw.verdict, raw.quote.strip(), tag)


def ground(
    skills: Sequence[ContractSkill],
    needs_by_ref: Mapping[str, Any],
    raw: RawJudgement,
    resume_text: str,
) -> GroundedJudgement:
    """Apply rules 1 to 5 to one candidate's reading. Pure.

    `needs_by_ref` maps each listed need's ref to its `NamedNeed` (anything
    with a `source`). `raw.skills` is keyed by the skill's id; a skill the
    reading does not mention is treated as `none` (the judge refuses a reading
    that omits a skill, so this only happens in a direct call).
    """
    index = ResumeIndex.of(resume_text)
    ungrounded: list[Mapping[str, str]] = []
    contradicted: list[str] = []
    refused: list[Mapping[str, str]] = []

    grounded_skills = tuple(
        _ground_skill(skill, raw.skills.get(skill.id), index, ungrounded, contradicted)
        for skill in skills
    )
    negatives = tuple(
        g.skill
        for g in grounded_skills
        if g.verdict == config.VERDICT_NONE
        and g.skill.bucket == BUCKET_MUST_HAVE
        and not skill_mentioned(g.skill.name, index)
    )
    experience = _ground_item(
        config.TAG_KIND_EXPERIENCE,
        config.TAG_KIND_EXPERIENCE,
        raw.experience,
        index,
        ungrounded,
        refused,
    )
    role_fit = _ground_item(
        config.TAG_KIND_ROLE_FIT,
        config.TAG_KIND_ROLE_FIT,
        raw.role_fit,
        index,
        ungrounded,
        refused,
    )

    needs: list[GroundedNeed] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for item in raw.needs:
        listed = needs_by_ref.get(item.need_ref)
        if listed is None:
            unknown.append(str(item.need_ref)[:16])
            continue
        if item.need_ref in seen:
            continue
        seen.add(item.need_ref)
        grounded = _ground_item(
            config.TAG_KIND_COMPANY_NEED,
            item.need_ref,
            RawItem(item.verdict, item.quote, item.tag),
            index,
            ungrounded,
            refused,
        )
        if grounded is None:
            continue
        needs.append(
            GroundedNeed(
                need_ref=item.need_ref,
                source=str(getattr(listed, "source", "")),
                verdict=grounded.verdict,
                quote=grounded.quote,
                tag=grounded.tag,
            )
        )

    return GroundedJudgement(
        skills=grounded_skills,
        experience=experience,
        role_fit=role_fit,
        needs=tuple(needs),
        negative_skills=negatives,
        ungrounded=tuple(ungrounded),
        contradicted_negative=tuple(contradicted),
        tags_refused=tuple(refused),
        unknown_needs=tuple(unknown),
    )

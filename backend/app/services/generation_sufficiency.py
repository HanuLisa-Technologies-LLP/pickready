"""The deterministic sufficiency gate that runs BEFORE every user-facing generator.

PROVENANCE
----------
`ai-upgrade-spec-doc.md`, section "case 2". Owner document. It names one defect
class and requires two fixes for it, an architectural one and a promptable one;
this module is the architectural half, and the `## Examples` block now carried
by every prompt named below is the other.

THE DEFECT CLASS
----------------
A generator handed thin evidence writes its own hedging into the field a person
reads: "The retrieved material does not establish what this organization does",
"candidates should not infer", "this is unverified". That is the model narrating
its own reasoning process onto a production surface, and on the company profile
it is a PUBLIC one.

Asking a prompt not to do it is not a control. The model only reaches for that
sentence when it has nothing to write, so the fix is to decide whether there is
anything to write BEFORE the prompt runs, in ordinary code, and to skip
generation entirely when the answer is no.

  sufficient    -> generation runs, and the prompt may state plainly that the
                   question "is there enough material" was already answered yes.
  insufficient  -> generation does not run. The caller returns a FIXED
                   empty-state key from `EMPTY_STATE_COPY`, never model text and
                   never freeform text of its own.

AN EMPTY STATE IS A FACT ABOUT THE RECORD, NOT A CONFIDENCE
-----------------------------------------------------------
The distinction the whole module rests on. "No answer was recorded for this item
in the assessment" is a statement about a table, is true, and is reviewed copy.
"The retrieved material does not establish" is a statement about the model's own
reading of its sources, is unfalsifiable, and is generated. The first is
allowed and lives in the catalogue below; the second is banned everywhere,
including in this catalogue and in the prompt files, and
`tests/test_no_meta_commentary.py` sweeps both rather than checking a call site.

PER FIELD, NEVER GLOBALLY
-------------------------
Every gate here answers per section or per field. A globally-passing check masks
one weak section, and a globally-failing one throws away a strong section
because something else on the same entity was thin. `company_profile_states`
returns three verdicts, `jd_document_states` returns seven.

THE INVENTORY THIS GATES (Step 1)
---------------------------------
Every module that calls `llm_router` for output a person reads, the prompt file
it sends, its task type, and where the output is displayed. `surface` decides
REGISTER, which is the second thing the few-shot blocks teach: marketing voice
on a public page, neutral and precise in an internal report.

| module                        | prompt file                     | task_type                 | surface          | gate |
|-------------------------------|---------------------------------|---------------------------|------------------|------|
| services/company_research     | company_research_system.txt     | company_profile_research  | public           | `company_profile_states` |
| services/jd_generation        | jd_document.txt                 | jd_generation             | public           | `jd_document_states` |
| services/jd_generation        | jd_generation_system.txt        | jd_generation             | public           | `jd_json_states` |
| services/gap_analysis         | report_gap_probes.txt           | report_synthesis          | internal         | `gap_probe_state` |
| services/hiring/sutra         | sutra_skills_draft.txt          | skills_drafting           | internal         | `swot_analysis.is_saved` (a saved SWOT) |
| services/hiring/sutra         | sutra_assessment_context.txt    | assessment_context        | internal, hidden | `skills.validate_for_save` |
| assessment_formats/coding_generation | coding_question_generation.txt | coding_question_generation | candidate-facing | `code_execution.is_enabled()` and a skill name and evidence line |
| services/yukti/judge          | yukti_matching_system.txt       | yukti_matching            | internal         | `grounding` over every tag and quote; a failure is `not_assessed`, never a default |
| assessment_questions/generate | assessment_question_generation.txt | question_generation | candidate-facing | a saved, invited contract (`ContractNotReady`, `LookupError`); a template, recorded, for any slot not written |
| services/swot_analysis        | swot_analysis_system.txt        | swot_analysis             | internal         | `swot_input_state` |
| services/outreach_content     | outreach_email_system.txt       | email_composition         | candidate-facing | `outreach_state` |
| services/outreach_content     | email_generation.txt            | email_composition         | candidate-facing | `outreach_state` |
| services/lifecycle_email      | email_application_confirmation  | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_assessment_invitation     | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_assessment_reminder       | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_assessment_complete       | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_shortlist                 | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_rejected                  | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_hold                      | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_interview_scheduled       | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_interview_completed       | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_offer_extended            | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_joined                    | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_databank_invitation       | email_composition         | candidate-facing | `lifecycle_email_state` |
| services/lifecycle_email      | email_question_bank_reminder    | email_composition         | internal         | `lifecycle_email_state` |
| services/email_templates      | none, a fixed catalogue in code | none                      | candidate-facing | no model call, nothing to gate |

Generators OUTSIDE this change, listed so the next reader does not think the
sweep was complete: the live conversation (`services/interviewer`,
`services/ppi_interview`), the PRISM remarks themselves
(`services/siddhi`, `services/functional_assessment`), the six question formats
(`services/assessment_formats`), project evidence (`services/projects`), BD
reach (`services/web_research`), resume and reply extraction, and
`services/matching_categories`. Each is owned elsewhere in this programme and
gating a generator whose call site another change owns would be half a change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from app.models.email_log import (
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_ASSESSMENT_REMINDER,
    EMAIL_TYPE_DATABANK_INVITATION,
    EMAIL_TYPE_INTERVIEW_SCHEDULED,
    EMAIL_TYPE_PROMPTS,
    EMAIL_TYPE_QUESTION_BANK_REMINDER,
    EMAIL_TYPE_SHORTLIST,
)
from app.services import agent_loop
from app.services.agent_loop import Defect

__all__ = [
    "BAD_EXAMPLE_CLOSE",
    "BAD_EXAMPLE_OPEN",
    "EMPTY_STATE_COPY",
    "EXAMPLES_HEADING",
    "GATED_PROMPTS",
    "strip_bad_examples",
    "SWOT_MIN_JD_WORDS",
    "swot_input_state",
    "GENERIC_STRENGTHS_PLACEHOLDER",
    "META_COMMENTARY_PHRASES",
    "META_COMMENTARY_WORDS",
    "MIN_ATTRIBUTABLE_SOURCES",
    "NAME_MATCH_RATIO",
    "Sufficiency",
    "attributable_sources",
    "company_profile_states",
    "empty_state_copy",
    "entity_matches",
    "host_matches",
    "gap_probe_state",
    "jd_document_states",
    "jd_json_states",
    "lifecycle_email_state",
    "meta_commentary_defects",
    "outreach_evidence",
    "outreach_state",
    "OUTREACH_EVIDENCE_KEYS",
]


#: Every prompt file whose generator this module gates, which is the inventory
#: table above with its email rows expanded. Declared as data so the sweeps in
#: `tests/test_no_meta_commentary.py` run over the WHOLE set rather than over a
#: list somebody remembered to extend: a rule enforced at one call site is a rule
#: the next entry breaks. Adding a generator to the inventory adds it to the
#: sweep, to the few-shot requirement and to the banned-phrase check at once.
GATED_PROMPTS: tuple[str, ...] = (
    "company_research_system",
    "jd_document",
    "jd_generation_system",
    "report_gap_probes",
    "outreach_email_system",
    # Sutra (Vivekium release): the Skills draft and the hidden assessment
    # context. Internal surfaces, and exactly where a model with thin input
    # would otherwise narrate its own uncertainty into what Vaada reads.
    "sutra_skills_draft",
    "sutra_assessment_context",
    # Yukti (Vivekium release, Phase 2): its tags reach a recruiter.
    "yukti_matching_system",
    "email_generation",
    # Phase 4 (Vivekium release): the executed coding question. Its statement
    # is candidate-facing, and a model narrating how thin the role summary was
    # would put that narration in front of every candidate on the job.
    "coding_question_generation",
    # Phase 3 WP2 (Vivekium release): the per-candidate prose questions. Every
    # line is read by a candidate, so a model narrating the resume it was
    # given would put that narration in front of the person being assessed.
    "assessment_question_generation",
) + tuple(sorted(EMAIL_TYPE_PROMPTS.values()))

#: The few-shot block every gated prompt carries, and the fence around the one
#: part of it that is allowed to contain banned language.
#:
#: A PLAIN UPPERCASE HEADING, NOT `## Examples`. `prompts/registry.py` drops
#: every line that starts with `#` at column zero, by design, so a markdown
#: heading written there is silently deleted from the prompt that states it.
#: `app/prompts/__init__.py` does not drop it, so the two loaders would disagree
#: about what the model was shown. One convention, readable under both.
EXAMPLES_HEADING = "EXAMPLES"

#: The BAD example reproduces the failure mode verbatim, which means it contains
#: the exact phrases the repository sweep bans. The sweep therefore excises this
#: fenced region and checks the rest: the demonstration is the point, and a
#: prompt that could not show the model what not to write would teach it less.
#: `tests/test_no_meta_commentary.py` also asserts the region is NOT empty of
#: banned language, so a fence cannot be used to smuggle ordinary text past the
#: sweep.
BAD_EXAMPLE_OPEN = "BAD EXAMPLE, DO NOT WRITE ANYTHING LIKE THIS"
BAD_EXAMPLE_CLOSE = "END BAD EXAMPLE"


def strip_bad_examples(text: str) -> str:
    """The prompt with its fenced BAD example regions removed.

    Fails closed on an unbalanced fence: an unclosed opener drops the rest of
    the file, so a missing `END BAD EXAMPLE` shows up as a prompt whose later
    rules vanished rather than as a silently widened exemption.
    """
    kept: list[str] = []
    inside = False
    for line in (text or "").splitlines():
        if BAD_EXAMPLE_OPEN in line:
            inside = True
            continue
        if BAD_EXAMPLE_CLOSE in line:
            inside = False
            continue
        if not inside:
            kept.append(line)
    return "\n".join(kept)


# ── The verdict ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Sufficiency:
    """Whether one field or section may be generated, and what to render if not.

    `reason` is OPERATOR text. It is logged and it is never rendered: a reason
    is exactly the sentence this whole module exists to keep off a page, and
    letting it out of a log would reintroduce the defect through the door
    marked "helpful detail".
    """

    sufficient: bool
    empty_state_key: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.sufficient and self.empty_state_key is not None:
            raise ValueError("a sufficient verdict carries no empty-state key")
        if not self.sufficient:
            if self.empty_state_key not in EMPTY_STATE_COPY:
                raise ValueError(
                    f"unknown empty-state key {self.empty_state_key!r}; add it "
                    "to EMPTY_STATE_COPY so the UI has fixed copy to render"
                )

    def __bool__(self) -> bool:
        return self.sufficient


def _ok() -> Sufficiency:
    return Sufficiency(True)


def _no(key: str, reason: str) -> Sufficiency:
    return Sufficiency(False, key, reason)


# ── The fixed empty-state catalogue ──────────────────────────────────────────
#
# One key per field that can be skipped, and reviewed copy for each. A caller
# returns the KEY; the copy travels with it so a surface that has no component
# for the key still has something honest to print.
#
# Every entry states a fact about the record or an instruction to the person
# reading it. None describes a model, a source, a confidence or a sufficiency
# decision, and `tests/test_no_meta_commentary.py` sweeps this whole dict rather
# than trusting that sentence. No number and no em dash, like every other string
# that can reach a client.

EMPTY_STATE_COPY: dict[str, str] = {
    # company profile, public
    "company_profile.about_company.no_sources": (
        "Write this section yourself. Adding the company website to the "
        "profile and running the research again usually helps."
    ),
    "company_profile.work_life.no_sources": (
        "Write this section yourself. Describe the hours, the location, the "
        "rhythm of the week, and how much autonomy people have."
    ),
    "company_profile.benefits.no_sources": (
        "Write this section yourself. List what the company offers beyond "
        "salary."
    ),
    # job description, public
    "job_description.description.no_brief_detail": "To be written by the hiring team.",
    "job_description.role.no_brief_detail": "To be written by the hiring team.",
    "job_description.responsibilities.no_brief_detail": (
        "To be confirmed by the hiring team."
    ),
    "job_description.accountabilities.no_brief_detail": (
        "To be confirmed by the hiring team."
    ),
    "job_description.education.no_brief_detail": (
        "To be confirmed by the hiring team."
    ),
    "job_description.skills.no_brief_detail": "To be confirmed by the hiring team.",
    "job_description.experience.no_brief_detail": (
        "To be confirmed by the hiring team."
    ),
    # job SWOT, internal: refused before the model is called
    "swot.jd_too_thin": (
        "Write the job description first. The SWOT is drafted from it, so it "
        "needs a title and a few paragraphs describing the role."
    ),
    # gap analysis, internal
    "gap_analysis.probes.no_recorded_answer": (
        "No answer was recorded for this item in the assessment."
    ),
    # outreach and lifecycle email, candidate-facing
    "outreach_email.no_recorded_evidence": (
        "Sent from the standard wording for this stage."
    ),
    "lifecycle_email.no_recorded_strengths": (
        "Sent from the standard wording for this stage."
    ),
    "lifecycle_email.no_link_available": (
        "Sent from the standard wording for this stage."
    ),
    "lifecycle_email.no_scheduled_time": (
        "Sent from the standard wording for this stage."
    ),
}


class UnknownEmptyState(KeyError):
    """A key with no catalogue entry.

    Its own type, and raised rather than defaulted, for the reason the whole
    module exists: a caller that silently invented copy for a missing key would
    be writing freeform text into the one place freeform text is banned.
    """


def empty_state_copy(key: str) -> str:
    """The reviewed sentence for one empty-state key. Raises on an unknown key."""
    try:
        return EMPTY_STATE_COPY[key]
    except KeyError as exc:
        raise UnknownEmptyState(
            f"no empty-state copy for {key!r}; add it to EMPTY_STATE_COPY"
        ) from exc


# ── The meta-commentary corpus, and the ONE matcher over it ──────────────────
#
# Derived from an audit of the failure outputs the specification quotes and of
# the strings this codebase itself was feeding into prompts and templates:
#
#   * "The retrieved material does not establish what this organization does",
#     "candidates should not infer", "this is unverified" -- quoted verbatim in
#     `ai-upgrade-spec-doc.md` "case 2" as the observed company-research output.
#   * "No specific evidence was recorded for this category." --
#     `outreach_content._candidate_evidence`, which interpolated that sentence
#     straight into a candidate's email through `_template_content`. Fixed in
#     the same change; the phrase stays banned so it cannot come back.
#   * "the content does not cover a section" and "return an empty string for a
#     section the content genuinely cannot support" -- the instructions the old
#     `company_research_system` prompt gave, which is where the model learned
#     that describing the gap was an acceptable answer.
#
# PHRASES ARE THREE WORDS OR MORE, every one of them, because
# `agent_loop.banned_phrase_gate` allows a match window one word narrower than
# the phrase with a three-word floor: a two-word entry contributes nothing the
# exact match does not already catch, and a one-word entry would fire on
# ordinary English.

META_COMMENTARY_PHRASES: tuple[str, ...] = (
    "does not establish",
    "do not establish",
    "does not confirm",
    "cannot be verified",
    "could not be verified",
    "cannot be confirmed",
    "the retrieved material",
    "the retrieved content",
    "the retrieved sources",
    "the available sources",
    "the sources provided",
    "the provided material",
    "the supplied material",
    "based on the sources",
    "drawn from the material provided",
    "candidates should not infer",
    "readers should not infer",
    "should not be inferred",
    "no information was found",
    "no information is available",
    "no details were found",
    "no evidence was found",
    "no specific evidence was recorded",
    "not enough information to",
    "insufficient information to",
    "there is not enough evidence",
    # NOT "it is not clear". `banned_phrase_gate` counts a three-word window
    # CONTAINED in the phrase, so that entry fires on the window "it is not",
    # which is ordinary English and appears in copy this product legitimately
    # sends ("it is not a test you can fail"). Anchoring on "from" keeps the
    # hedge and drops the false positive.
    "is not clear from",
    "it is unclear whether",
    "i was unable to determine",
    "i could not find any",
    "we could not find any",
    "this could not be determined",
    "could not be determined from",
    "based on the information available",
    "with the information available to",
    "as far as can be determined",
    "appears to be the case",
    "it should be noted that",
    "please note that this",
    "this draft is based on",
    "further verification is required",
    "further research is needed",
    "requires independent verification",
)

#: Single words that describe the STATUS OF EVIDENCE rather than the subject,
#: and therefore never belong in generated copy on any of these surfaces. Kept
#: apart from the phrases above because `banned_phrase_gate`'s three-word floor
#: exists for phrase matching and a word needs a word-boundary match instead;
#: `meta_commentary_defects` is the one function that runs both, so there is
#: still exactly one place a caller asks the question.
META_COMMENTARY_WORDS: frozenset[str] = frozenset(
    {
        "unverified",
        "unsubstantiated",
        "unsourced",
        "uncorroborated",
        "unconfirmed",
    }
)

_WORD_RE = re.compile(r"[a-z']+")


def _normalised_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").casefold())


def meta_commentary_defects(
    text: str, *, location: str = "output"
) -> tuple[Defect, ...]:
    """Every meta-commentary hit in one piece of generated text.

    THE ONE PLACE the corpus is applied, so a generator's runtime gate and the
    repository sweep in `tests/test_no_meta_commentary.py` cannot disagree about
    what the rule is. Returns `agent_loop.Defect`s so a caller can hand them
    straight to `agent_loop.reject_defects` and the next attempt is told, in the
    loop's own contract, exactly which sentence to remove.

    Phrase matching goes through `agent_loop.banned_phrase_gate`, which is this
    repository's one banned-phrase implementation and already handles
    punctuation, casing and a bounded close variant. Word matching is a
    word-boundary test here, because that gate's three-word floor is deliberate
    and lowering it would make "team" match "team player" again.
    """
    defects = list(
        agent_loop.banned_phrase_gate(
            text, META_COMMENTARY_PHRASES, location=location
        ).defects
    )
    present = META_COMMENTARY_WORDS.intersection(_normalised_words(text))
    for word in sorted(present):
        defects.append(
            Defect(
                "meta_commentary",
                location,
                f"remove {word!r}: describe the subject, never the standing of "
                "the evidence behind it",
            )
        )
    return tuple(defects)


# ── The entity and attribution guard (Step 4) ────────────────────────────────
#
# "Foo IT", "Foo Group" and "Foo Enterprises" are DIFFERENT COMPANIES from
# "Foo Corp", and a search engine returns all four for the same query. A source
# that is not about the target entity is not evidence about the target entity,
# and letting one count is how a public profile ends up describing somebody
# else's business under this client's name.

#: Legal-form tokens. These may differ between two names for the same company
#: ("Foo Corp", "Foo Corporation", "Foo Pvt Ltd") and are dropped before the
#: comparison. Everything NOT on this list is treated as part of the name.
_LEGAL_FORMS: frozenset[str] = frozenset(
    {
        "co", "corp", "corporation", "company", "inc", "incorporated",
        "ltd", "limited", "llc", "llp", "plc", "pvt", "private",
        "gmbh", "ag", "sa", "sarl", "srl", "bv", "nv", "oy", "ab", "as",
        "pte", "kk", "the", "and",
    }
)

#: Tokens that, appended to a company name, name a DIFFERENT company. This is
#: the list the guard exists for. Unlike a legal form, "IT" or "Group" after
#: "Foo" is not a variant spelling of Foo, it is Foo's sibling, subsidiary,
#: former parent, or an unrelated business that picked the same first word.
_ENTITY_DESCRIPTORS: frozenset[str] = frozenset(
    {
        "it", "group", "enterprises", "enterprise", "technologies", "technology",
        "solutions", "systems", "services", "consulting", "consultancy",
        "industries", "ventures", "partners", "holdings", "labs", "laboratories",
        "software", "infotech", "associates", "international", "global",
        "digital", "media", "capital", "foundation", "trust", "bank",
        "healthcare", "motors", "steel", "energy", "logistics", "retail",
    }
)

#: How close two names must be to count as the same one, on `difflib`'s ratio
#: over the core tokens. Set for TYPO tolerance and nothing wider: "foo" against
#: "fooo" scores 0.857 and passes, "foo" against "bar" scores 0.0 and does not.
#: The structural rules above it, not this number, are what separate "Foo Corp"
#: from "Foo IT", because those two names are character-for-character close and
#: any ratio loose enough to reject them would reject real variants.
NAME_MATCH_RATIO = 0.82

#: How many attributable sources the `about_company` section needs. One page
#: naming a company can be a directory stub or an aggregator listing that says
#: nothing about the business; the section every candidate reads before applying
#: should rest on more than one. The topic sections take one, because a single
#: employee-review page IS the source for what the work is like.
MIN_ATTRIBUTABLE_SOURCES = 2

#: The separators a search result's title uses between the entity name and the
#: site name. The two dashes are built from `chr` rather than typed, which is the
#: standing rule for a character class that MATCHES a dash: a repo-wide em dash
#: sweep must not be able to rewrite the code that splits on one.
_SPLIT_RE = re.compile(
    "[|,:/" + chr(8211) + chr(8212) + chr(183) + ">" + chr(8226) + "]"
    + r"|\s-\s"
)
_TOKEN_RE = re.compile(r"[a-z0-9&+]+")


def _name_tokens(name: str) -> tuple[str, ...]:
    """A name reduced to its identifying tokens, legal forms dropped."""
    tokens = _TOKEN_RE.findall((name or "").casefold())
    return tuple(token for token in tokens if token not in _LEGAL_FORMS)


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def entity_matches(target: str, candidate: str) -> bool:
    """Whether `candidate` names the same organisation as `target`.

    Deterministic, and deliberately conservative in one direction: dropping a
    source that was about the target costs a thinner section, while keeping one
    that was about a similarly named company puts another business's description
    on this client's public page.

    A candidate matches when the target's tokens appear in it as a contiguous
    run and neither token adjacent to that run is an entity descriptor. So
    "Foo Corp Reviews" matches "Foo Corp" and "Foo IT" does not.
    """
    wanted = _name_tokens(target)
    if not wanted:
        return False
    found = _name_tokens(candidate)
    width = len(wanted)
    if len(found) < width:
        return False
    joined = " ".join(wanted)
    for start in range(len(found) - width + 1):
        window = found[start : start + width]
        if _ratio(" ".join(window), joined) < NAME_MATCH_RATIO:
            continue
        before = found[start - 1] if start else None
        after = found[start + width] if start + width < len(found) else None
        if before in _ENTITY_DESCRIPTORS or after in _ENTITY_DESCRIPTORS:
            continue
        return True
    return False


def _title_names(hit: Mapping[str, Any]) -> list[str]:
    """The names a search result's title offers, split on its separators.

    A result is routinely "Foo Corp Reviews | Glassdoor" or
    "Working at Foo Corp, Careers", and each segment is a candidate name.
    """
    return [
        segment.strip()
        for segment in _SPLIT_RE.split(str(hit.get("title") or ""))
        if segment.strip()
    ]


def _host_label(url: str) -> str:
    """The registrable label of a URL's host, or an empty string."""
    host = urlparse(str(url or "")).netloc.casefold()
    host = host[4:] if host.startswith("www.") else host
    parts = [part for part in host.split(".") if part]
    return parts[-2] if len(parts) >= 2 else ""


def host_matches(target: str, url: str) -> bool:
    """Whether a URL sits on the target company's own domain.

    A separate comparison from `entity_matches` because a host has no spaces:
    "Foo Corp" registers `foocorp.com` and `foo.com`, so the target is joined
    both with and without its legal form and the closer of the two is used.

    The descriptor rule survives the join, which is the point. `haldenit.com`
    is "halden" followed by "it", and "it" is what makes two companies
    different, so it is refused even though the string is one character-level
    edit away from `halden.com`. A ratio alone could not tell those apart.
    """
    label = _host_label(url)
    if not label:
        return False
    core = "".join(_name_tokens(target))
    full = "".join(_TOKEN_RE.findall((target or "").casefold()))
    if not core:
        return False
    for candidate in {core, full}:
        if not candidate:
            continue
        if label == candidate:
            return True
        if label.startswith(candidate):
            remainder = label[len(candidate) :]
            if remainder in _ENTITY_DESCRIPTORS:
                return False
            if remainder in _LEGAL_FORMS:
                return True
        if _ratio(label, candidate) >= NAME_MATCH_RATIO:
            # A close but not exact label still has to survive the descriptor
            # rule, checked on the extra characters the label carries.
            remainder = label[len(candidate) :] if label.startswith(candidate) else ""
            if remainder in _ENTITY_DESCRIPTORS:
                return False
            return True
    return False


def attributable_sources(
    target: str, hits: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """The retrieved pages that are actually about `target`.

    A page whose title names a different company is dropped here rather than
    fenced into the prompt with a warning: a warning is a request, and the model
    has the page.
    """
    return [
        hit
        for hit in hits
        if host_matches(target, str(hit.get("url") or ""))
        or any(entity_matches(target, name) for name in _title_names(hit))
    ]


# ── Company profile (public) ─────────────────────────────────────────────────
#
# Per section, because the three ask different questions of the same pages: a
# Wikipedia entry answers `about_company` and says nothing about benefits, and a
# Glassdoor page is the reverse.

_WORK_LIFE_TERMS: tuple[str, ...] = (
    "work life", "work-life", "culture", "hybrid", "remote", "onsite",
    "on-site", "office", "hours", "shift", "flexib", "team", "manager",
    "wfh", "commute", "workload", "autonomy", "day to day",
)

_BENEFIT_TERMS: tuple[str, ...] = (
    "benefit", "insurance", "medical", "health cover", "leave", "holiday",
    "pto", "pension", "provident fund", "esop", "stock", "bonus", "perks",
    "wellness", "creche", "childcare", "relocation", "learning budget",
)

_SECTION_TERMS: dict[str, tuple[str, ...]] = {
    "work_life": _WORK_LIFE_TERMS,
    "benefits": _BENEFIT_TERMS,
}


def _mentions(hit: Mapping[str, Any], terms: Iterable[str]) -> bool:
    blob = f"{hit.get('title') or ''} {hit.get('content') or ''}".casefold()
    return any(term in blob for term in terms)


def company_profile_states(
    company: str, hits: Sequence[Mapping[str, Any]]
) -> dict[str, Sufficiency]:
    """One verdict per profile section, over the pages attributable to `company`.

    `about_company` needs `MIN_ATTRIBUTABLE_SOURCES` pages about this company.
    `work_life` and `benefits` each need one attributable page that actually
    discusses the topic, because a page that names the company and never
    mentions leave, insurance or hours is not evidence about its benefits, and
    asking a model to write that section from it is asking it to invent one.
    """
    attributable = attributable_sources(company, hits)
    states: dict[str, Sufficiency] = {}

    states["about_company"] = (
        _ok()
        if len(attributable) >= MIN_ATTRIBUTABLE_SOURCES
        else _no(
            "company_profile.about_company.no_sources",
            f"attributable_sources={len(attributable)} of {len(hits)} retrieved",
        )
    )
    for section, terms in _SECTION_TERMS.items():
        on_topic = [hit for hit in attributable if _mentions(hit, terms)]
        states[section] = (
            _ok()
            if on_topic
            else _no(
                f"company_profile.{section}.no_sources",
                f"on_topic_sources=0 of {len(attributable)} attributable",
            )
        )
    return states


# ── Job description (public) ─────────────────────────────────────────────────
#
# The brief is a structured form, so "is there anything to write this section
# from" is answerable exactly rather than approximately. A section with no
# backing field gets the catalogue line and the prompt is told not to write it,
# which is the reversal of the old rule 8 ("write the most reasonable
# professional content you can rather than leaving a section empty") -- an
# instruction to invent, on a document candidates apply against.

def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _has_items(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(str(item or "").strip() for item in value)
    return False


def _section_states(backing: Mapping[str, bool], prefix: str) -> dict[str, Sufficiency]:
    return {
        section: (
            _ok()
            if backed
            else _no(
                f"{prefix}.{section}.no_brief_detail",
                f"the brief carries no field backing {section}",
            )
        )
        for section, backed in backing.items()
    }


def jd_document_states(brief: Mapping[str, Any]) -> dict[str, Sufficiency]:
    """One verdict per `##` section of the unified JD document.

    Keyed by the lowercased heading, so `jd_generation.JD_SECTIONS` stays the
    one place the headings and their order are declared.
    """
    brief = brief or {}
    title = _has_text(brief.get("title"))
    skills = _has_items(brief.get("skills"))
    context = title or _has_text(brief.get("department")) or _has_text(
        brief.get("reporting_to")
    )
    years = brief.get("experience_min_years") is not None or (
        brief.get("experience_max_years") is not None
    )
    return _section_states(
        {
            "description": title,
            "role": context,
            "responsibilities": skills or title,
            "accountabilities": skills or title,
            # There is NO education field on this brief, so this section has
            # never had anything behind it. It was written anyway, every time,
            # from nothing. The catalogue line is the honest replacement and a
            # recruiter fills it in the editor they already open.
            "education": _has_text(brief.get("education")),
            "skills": skills,
            "experience": years,
        },
        "job_description",
    )


def jd_json_states(brief: Mapping[str, Any]) -> dict[str, Sufficiency]:
    """The same question for the older per-key JD path (`generate_job_description`).

    A different brief shape, so a separate function rather than a parameter: one
    of them carries `requirements` and `education`, the other carries
    `experience_min_years`, and a single function taking both would have to
    guess which caller it was serving.
    """
    brief = brief or {}
    title = _has_text(brief.get("title"))
    requirements = _has_items(brief.get("requirements"))
    skills = _has_items(brief.get("skills"))
    return _section_states(
        {
            "description": title,
            "role": title,
            "responsibilities": requirements or skills,
            "accountabilities": requirements or skills,
            "education": _has_text(brief.get("education")),
            "skills": skills,
            "experience": brief.get("experience") is not None
            or brief.get("experience_years") is not None,
        },
        "job_description",
    )


# ── Gap analysis (internal) ──────────────────────────────────────────────────


def gap_probe_state(evidence: Sequence[Mapping[str, str]]) -> Sufficiency:
    """Whether this gap has an answer to ground a probe in.

    The specification's rule for this generator is "at least one graded item in
    the relevant band", and `gap_analysis.gap_items` already answers the band
    half by construction: an item that is not in the band is never passed here.
    What it did NOT answer is whether the candidate said anything about it, and
    the prompt's own instruction in that case was to "probe the thinness
    itself" -- which is a probe about the assessment rather than about the
    person, written by a model with nothing else available.
    """
    answered = [row for row in evidence if str(row.get("answer") or "").strip()]
    if answered:
        return _ok()
    return _no(
        "gap_analysis.probes.no_recorded_answer",
        f"answers_recorded=0 of {len(evidence)} exchanges",
    )


# ── The Job SWOT (internal) ───────────────────────────────────────────────────

#: The JD body a SWOT can be drafted from, in words, after the markdown
#: headings are removed. A JD that is a title and seven empty headings gives the
#: model nothing to analyse, and the SWOT it writes from nothing is exactly the
#: invented company fact the SWOT prompt forbids. Sixty words is a short
#: paragraph: low enough that no real JD is refused, high enough that a
#: skeleton is.
SWOT_MIN_JD_WORDS = 60

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.MULTILINE)


def swot_input_state(title: str | None, jd_markdown: str | None) -> Sufficiency:
    """Whether a Job SWOT may be drafted from this job's title and JD document.

    Decided BEFORE the dispatch, so a refused request spends no model call and
    leaves the SWOT row exactly as it was. Counts words in the body with every
    markdown heading line removed: the seven fixed section headings are
    structure, not description.
    """
    if not _has_text(title):
        return _no("swot.jd_too_thin", "the job has no title")
    body = _HEADING_RE.sub(" ", str(jd_markdown or ""))
    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'+#./-]*", body))
    if words < SWOT_MIN_JD_WORDS:
        return _no(
            "swot.jd_too_thin",
            f"jd_body_words={words} below {SWOT_MIN_JD_WORDS}",
        )
    return _ok()


# ── Outreach and lifecycle email (candidate-facing) ──────────────────────────

#: The sentence `api/emails._strengths_prose` returns when the ranking carries
#: no comments, and the default `lifecycle_email` uses for the same field. It is
#: not evidence, it is the ABSENCE of evidence wearing evidence's clothes, and a
#: prompt that says "name TWO specific strengths drawn from the evidence above"
#: given only this has been asked to invent two. Declared here so the gate, the
#: default and the API all name the same string.
GENERIC_STRENGTHS_PLACEHOLDER = "strong, relevant experience for this role"

#: Which context key each email type needs a REAL value for before a model is
#: asked to write the claim that key carries. An email type absent from this
#: table makes no evidence-backed claim: a rejection, a receipt and a welcome
#: are true from the transition alone, and there is nothing for a thin record to
#: make the model invent.
_EMAIL_REQUIRED_CONTEXT: dict[str, tuple[str, str]] = {
    EMAIL_TYPE_SHORTLIST: ("strengths", "lifecycle_email.no_recorded_strengths"),
    EMAIL_TYPE_ASSESSMENT_INVITATION: (
        "assessment_link",
        "lifecycle_email.no_link_available",
    ),
    EMAIL_TYPE_ASSESSMENT_REMINDER: (
        "assessment_link",
        "lifecycle_email.no_link_available",
    ),
    EMAIL_TYPE_DATABANK_INVITATION: (
        "application_link",
        "lifecycle_email.no_link_available",
    ),
    EMAIL_TYPE_QUESTION_BANK_REMINDER: (
        "job_link",
        "lifecycle_email.no_link_available",
    ),
    EMAIL_TYPE_INTERVIEW_SCHEDULED: (
        "scheduled_at",
        "lifecycle_email.no_scheduled_time",
    ),
}

#: Values that are present in the context and still are not evidence. Every one
#: is a default this product itself supplies so a prompt can render; feeding one
#: to a model is feeding it a placeholder and calling it a fact.
_EMAIL_PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        GENERIC_STRENGTHS_PLACEHOLDER.casefold(),
        "a time the team will confirm",
    }
)


def lifecycle_email_state(email_type: str, context: Mapping[str, Any]) -> Sufficiency:
    """Whether this email's one evidence-backed claim has anything behind it.

    Scoped per email TYPE, which is the per-field scope for this generator: an
    email is one artifact and its claim is one field. Insufficient means the
    deterministic template is sent instead, which makes no claim it cannot keep
    and is the degradation path `lifecycle_email` already had for an outage.
    """
    required = _EMAIL_REQUIRED_CONTEXT.get(email_type)
    if required is None:
        return _ok()
    key, empty_state = required
    value = str((context or {}).get(key) or "").strip()
    if value and value.casefold() not in _EMAIL_PLACEHOLDER_VALUES:
        return _ok()
    return _no(empty_state, f"{key} was empty or a placeholder")


#: The ranking comments an outreach email personalises itself from.
OUTREACH_EVIDENCE_KEYS: tuple[str, ...] = (
    "skills_comment",
    "experience_comment",
    "role_comment",
    "education_comment",
)


def outreach_state(candidate: Mapping[str, Any]) -> Sufficiency:
    """Whether an outreach email has any recorded evidence to personalise from.

    `next_round` outreach exists to tell a candidate WHY they are moving on. With
    no comment on any of the four categories there is no why, and the previous
    behaviour was to hand the model the sentence "No specific evidence was
    recorded for this category." four times and ask it to highlight the
    candidate's strengths from that. One of those emails is in the corpus this
    module bans.
    """
    candidate = candidate or {}
    if any(str(candidate.get(key) or "").strip() for key in OUTREACH_EVIDENCE_KEYS):
        return _ok()
    return _no(
        "outreach_email.no_recorded_evidence",
        "no ranking comment was recorded on any category",
    )


def outreach_evidence(candidate: Mapping[str, Any]) -> dict[str, str]:
    """Only the categories that HAVE a comment, keyed as the prompt expects.

    The absent ones are omitted rather than filled with a sentence saying they
    are absent. A prompt that is told nothing about education writes nothing
    about education; a prompt that is told "no evidence was recorded for
    education" has been handed a sentence about the record and will sometimes
    pass it on.
    """
    candidate = candidate or {}
    return {
        key: str(candidate.get(key)).strip()
        for key in OUTREACH_EVIDENCE_KEYS
        if str(candidate.get(key) or "").strip()
    }


# ── Self-checks that would otherwise be discovered in production ─────────────
#
# Module scope on purpose, and cheap: every one is a property of the constants
# above and none of them reads a database or a model. They are asserted in
# `tests/test_generation_sufficiency.py` as well, because `python -O` strips an
# assert and a check that disappears in the production image is not a check.

assert not (_LEGAL_FORMS & _ENTITY_DESCRIPTORS), (
    "a token cannot be both an interchangeable legal form and the thing that "
    "makes two names different companies"
)
assert all(len(phrase.split()) >= 3 for phrase in META_COMMENTARY_PHRASES), (
    "every banned phrase needs three words; agent_loop.banned_phrase_gate has a "
    "three-word floor and a shorter entry is dead weight"
)
assert all(key in EMPTY_STATE_COPY for _, key in _EMAIL_REQUIRED_CONTEXT.values())

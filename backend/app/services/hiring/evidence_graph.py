"""Department Evidence Graphs, read from Part VI (spec-doc6 4.4, Vaada).

    "Question generation draws on the relevant Department Evidence Graph for
     the role's department, not a generic bank. Delete the generic bank in the
     same commit. Triangulation posture: each claim is something to corroborate,
     routed toward the evidence sources Sutra's matrix flagged as required for
     that competency."

WHAT WAS DELETED HERE, AND WHY IT WAS A GENERIC BANK
-----------------------------------------------------
Until this commit this module carried five hand-written node sets (generic,
engineering, sales, finance, operations) totalling twenty-nine nodes, plus a
`GRAPHS` dict whose lookup fell back to the GENERIC set for any department it
did not recognise. Not one `establishes` sentence, hollow tell or edge in it
came from RPN-PHIL-001, because the Runbook was absent when it was written.

That fallback is the failure the department models exist to prevent: a civil
engineer, a designer, an architect, an HR generalist and a tradesperson were
all routed to the same seven generic nodes, so the "department evidence graph"
named a department and delivered one bank. It is deleted, not deprecated.

WHAT IS HERE INSTEAD
--------------------
The Runbook's own fifteen department models, read from
`runbook_data/department_models.yaml`, which mirrors Part VI (sections 21 to 35)
and is held to it in both directions by `tests/test_runbook_parity.py`:

  * `EvidenceNode.establishes` is Part VI's OBSERVABLE EVIDENCE column, verbatim.
    That column is exactly "what we would see if this were true", which is what
    one line of questioning has to establish.
  * `DepartmentGraph.hollow_tells` is Part VI's GAMING VECTORS for that
    department (21.7, 22.7, 23.8, 24.6, 25.6, 27.7, 29.7, 30.7, 32.6).
  * `DepartmentGraph.red_flags` is Part VI's RED FLAGS, which the Runbook is
    explicit "route to review, never auto-reject" (21.8). Nothing here grades,
    scores or rejects; a tell routes to a more specific question.
  * `DepartmentGraph.validation_probes` and `probe_design_pairs` are Part VI's
    validation probes (22.6, 23.6, 27.6, 29.6) and 21.6's weak-versus-strong
    probe design table, which is the specificity gradient stated per claim type.

AN ABSENT SUBSECTION IS RECORDED AS ABSENT
--------------------------------------------
Part VI's coverage is uneven and the Runbook says so (67.8, mirrored as
`coverage_notes` in the data). Non-technical support and administrative work
(section 35) has no gaming vectors and no red flags at all; eight departments
have no validation probes. Those come back as EMPTY TUPLES rather than borrowing
another department's, because a borrowed tell is the vocabulary collapse in
miniature: it reads as department knowledge and is not.

WHAT A GRAPH NODE IS NOT
------------------------
Not a question. A question is what Vaada writes fresh, per candidate, in their
own conversational context. This codebase deleted its preset technical bank on
2026-08-06 and nothing here reintroduces one: a node says what to ESTABLISH and
where corroboration would have to come from, and the words are still Vaada's.

NO EDGES, AND NO SECOND PLANNER
---------------------------------
The previous implementation carried an `unlocks` field, edges from one
competency to the next, and a `next_target` that walked them. Part VI supplies
competency menus, evidence tiers, probes and flags per department; it supplies
NO edges, and 67.8 separately concedes that department coverage is uneven, so
the absence is not an oversight to read around. Twenty-nine invented edges
across five departments were tolerable as a marked assumption; a hundred and
forty-three across fifteen would be a fabricated graph wearing the Runbook's
citations.

`next_target` went with them, and for a second reason: it had no callers. The
coverage plan -- which item, in what order, how many -- is decided before the
conversation starts by `ppi.generate_candidate_questions`, and that is what
keeps two candidates on one job comparable. A second ordering function here
would be a planner nobody runs, and the day somebody ran it the two plans would
disagree.

SOURCE: RPN-PHIL-001 Part VI preamble (v1.3). There are no prerequisite EDGES
between competencies and none are implied: the structure is competency to
observable evidence to assessment route, which is what the menus carry. A model
ordering its competencies by dependency would assert that one cannot be
demonstrated without another, which is false often enough to be unfair,
particularly for the non-traditional trajectories section 40 protects.

TRIANGULATION IS COUNTED BY ORIGINATOR, NOT BY DOCUMENT
--------------------------------------------------------
`corroborated_by` is 38.1's six independence GROUPS minus the candidate's own
`self_written` claim, because a claim cannot corroborate itself (5.4). Two of
the remaining five are not reachable inside a ReadyPick assessment today and
`corroboration_targets` says so rather than dropping them: a competency the
platform can probe and cannot confirm is one Miti must hold confidence down on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from math import ceil as _ceil
from typing import Any, Iterable, Mapping, cast

from app.services.hiring import layers

__all__ = [
    "CLASS_CONFIRMATION",
    "CLASS_CONTRADICTION",
    "CLASS_DISCOVERY",
    "CLASS_GAP",
    "DepartmentGraph",
    "DepartmentUnmapped",
    "EvidenceNode",
    "ProbePair",
    "QUESTION_CLASSES",
    "REACHABLE_GROUPS",
    "ScorecardUnavailable",
    "SpecificityLevel",
    "corroboration_targets",
    "discriminator_levels",
    "department_keys",
    "extension_ceiling",
    "graph_for",
    "independence_groups",
    "minimum_discriminator_fraction",
    "next_specificity_level",
    "probe_level",
    "node_for_competency",
    "nodes_for",
    "question_class",
    "required_evidence_sources",
    "resolve_department",
    "specificity_levels",
]


class DepartmentUnmapped(LookupError):
    """No Part VI department model covers this role, and none is substituted.

    Raised rather than defaulted. Section 36 states the procedure for adding a
    department ("added only through the following procedure, and never
    improvised mid-engagement"), so the correct response to a role outside the
    fifteen is a department model authored under that procedure, not the
    nearest-looking menu. A caller mid-conversation degrades to the question it
    already had; it never borrows another department's graph.
    """


class ScorecardUnavailable(RuntimeError):
    """Sutra's frozen matrix cannot be read, so its evidence routing is absent.

    Distinct from `DepartmentUnmapped` because the consequence is different: the
    department graph still applies and still says what to establish, and what is
    missing is only which SOURCES this job's matrix flagged as required for the
    competency. Named as its own class so a caller can catch exactly this and
    nothing else -- a broad except here would swallow a genuine data fault and
    report it as "Sutra not wired yet" forever.
    """


# -- Nodes, built from Part VI's competency menus ----------------------------


@dataclass(frozen=True)
class EvidenceNode:
    """What one line of questioning can establish, and what would confirm it."""

    #: The department this node belongs to, one of the fifteen Part VI keys.
    department: str
    #: Part VI's own stable id, e.g. "SW-02". Quoted so a reviewer can find the
    #: row it came from.
    competency_id: str
    #: The id lower-cased with hyphens folded, e.g. "sw_02". A stable key for a
    #: dict or a set, never shown to anyone.
    competency_key: str
    #: Part VI's competency name, e.g. "System design & architecture".
    name: str
    #: What a good answer would ESTABLISH, verbatim from Part VI's
    #: observable-evidence column. A fact about the candidate, never a question.
    establishes: str
    #: 38.1 independence groups that could corroborate it, the candidate's own
    #: `self_written` claim excluded (5.4: a claim cannot corroborate itself).
    corroborated_by: tuple[str, ...]
    #: The Runbook section this node was read from.
    source: str
    #: Position in Part VI's menu. Orders siblings of equal weight so the plan
    #: is total and deterministic; never a grade and never shown.
    ordinal: int = 0

    @property
    def key(self) -> str:
        return self.competency_key


@dataclass(frozen=True)
class ProbePair:
    """One row of 21.6: the retrievable probe and the participatory one.

    The Runbook's design rule for the pair is stated in the same section:
    "probe for specifics that only a participant would know, not for knowledge
    that is publicly retrievable". The weak column is kept, not discarded,
    because it is the only concrete statement of what a BAD probe looks like and
    `ppi_interview` refuses a generated question that reproduces one.
    """

    claim_type: str
    weak_probe: str
    strong_probe: str


@dataclass(frozen=True)
class DepartmentGraph:
    """One Part VI department model, as the conversation reads it."""

    key: str
    number: int
    title: str
    source: str
    nodes: tuple[EvidenceNode, ...]
    #: Part VI's gaming vectors. What an answer looks like when somebody has
    #: read about the work rather than done it. Routed to a more specific
    #: question, never to a grade.
    hollow_tells: tuple[str, ...] = ()
    #: Part VI's red flags. 21.8: "route to review, never auto-reject".
    red_flags: tuple[str, ...] = ()
    #: Literal validation probes, where Part VI prints them.
    validation_probes: tuple[str, ...] = ()
    #: 21.6's weak-versus-strong probe design table.
    probe_design_pairs: tuple[ProbePair, ...] = ()


def _departments() -> Mapping[str, Any]:
    return cast(Mapping[str, Any], layers.runbook_value("department_models", "departments"))


def department_keys() -> tuple[str, ...]:
    """The fifteen Part VI department keys, in Runbook section order."""
    entries = _departments()
    return tuple(
        sorted(entries, key=lambda key: int(entries[key]["number"]))
    )


def _competency_key(competency_id: str) -> str:
    return competency_id.strip().lower().replace("-", "_")


def _strings(entry: Mapping[str, Any], field: str) -> tuple[str, ...]:
    raw = entry.get(field) or ()
    return tuple(str(item) for item in raw if str(item).strip())


@lru_cache(maxsize=1)
def _graphs() -> dict[str, DepartmentGraph]:
    """Every department graph, built once from the extracted Part VI data.

    Cached because it is read on every question written and the underlying file
    does not change at runtime. `cache_clear` exists for a test that swaps the
    data.
    """
    built: dict[str, DepartmentGraph] = {}
    for key, entry in _departments().items():
        section = str(entry.get("competency_menu_source") or entry.get("source") or "")
        corroboration = tuple(
            group
            for group in independence_groups()
            if group != SELF_WRITTEN_GROUP
        )
        nodes = tuple(
            EvidenceNode(
                department=str(key),
                competency_id=str(row["id"]),
                competency_key=_competency_key(str(row["id"])),
                name=str(row["competency"]),
                establishes=str(row.get("observable_evidence") or ""),
                corroborated_by=corroboration,
                source=section,
                ordinal=index,
            )
            for index, row in enumerate(entry.get("competency_menu") or ())
        )
        built[str(key)] = DepartmentGraph(
            key=str(key),
            number=int(entry["number"]),
            title=str(entry["title"]),
            source=str(entry.get("source") or ""),
            nodes=nodes,
            hollow_tells=tuple(
                str(row.get("vector") or "")
                for row in entry.get("gaming_vectors") or ()
                if str(row.get("vector") or "").strip()
            ),
            red_flags=_strings(entry, "red_flags"),
            validation_probes=_strings(entry, "validation_probes"),
            probe_design_pairs=tuple(
                ProbePair(
                    claim_type=str(row["claim_type"]),
                    weak_probe=str(row["weak_probe"]),
                    strong_probe=str(row["strong_probe"]),
                )
                for row in entry.get("probe_design_pairs") or ()
            ),
        )
    return built


def graph_for(department_key: str) -> DepartmentGraph:
    """One department's graph, or a raise. NO GENERIC FALLBACK.

    Naming a civil engineer's competencies from IT & Software's menu, or from a
    generic one, is the vocabulary collapse the department models exist to
    prevent, and it would look like a successful lookup at every call site.
    """
    graphs = _graphs()
    try:
        return graphs[department_key]
    except KeyError as exc:
        raise DepartmentUnmapped(
            "No Part VI department model named %r. The Runbook carries %s, and "
            "section 36 requires a new department model to be authored through "
            "its own procedure rather than improvised."
            % (department_key, sorted(graphs))
        ) from exc


def nodes_for(department_key: str) -> tuple[EvidenceNode, ...]:
    return graph_for(department_key).nodes


# -- Resolving a role to one of the fifteen ----------------------------------
#
# THE VOCABULARY IS THE RUNBOOK'S OWN. Matching is done against Part VI's
# department titles, its role families and its competency names, so the resolver
# has no word list of its own to drift from the document. A role that matches
# nothing, or that matches two departments equally, raises: section 36 is
# explicit that a department outside the fifteen is added through a procedure,
# and guessing between two is how a mechanical engineer gets graded against the
# electrical menu.

#: Two words match when one is a prefix of the other and the shorter is at
#: least this long, so "engineer" matches "engineering" and "account" matches
#: "accounting" without a stemmer nobody would maintain.
#:
#: A FIXED-LENGTH STEM WAS TRIED FIRST AND WAS WRONG. Cutting every word to five
#: characters folds "general" and "generative" onto the same token, which routed
#: an "AI / Generative AI Engineer" to LEADERSHIP, GENERAL MANAGEMENT because
#: that department's title carries the other word. Prefix matching keeps them
#: apart: "general" is not a prefix of "generative".
_MIN_PREFIX = 4

#: A token appearing in more than this many departments carries no signal.
#: "engineering" is in five of the fifteen titles and "management" in four; a
#: token that common tells you nothing about which one a role belongs to.
_MAX_DEPARTMENT_FREQUENCY = 3

#: What each part of Part VI is worth when it matches. The order is the order of
#: specificity in the document: a role-family PHRASE ("quantity surveying",
#: "machine operators") names the job almost exactly, a department TITLE names
#: the function, a role-family word is a fragment of a name, and a competency
#: word is the noisiest of the four because a competency menu deliberately
#: reaches across neighbouring functions.
_ROLE_FAMILY_PHRASE_WEIGHT = 3.0
_TITLE_WEIGHT = 2.0
_ROLE_FAMILY_WEIGHT = 1.0
_COMPETENCY_WEIGHT = 0.5

#: How far ahead the winner must be. A margin rather than a strict inequality,
#: because one shared fragment ("engin", "manag") should not decide which
#: department a role belongs to. Below it the answer is DepartmentUnmapped, and
#: the caller degrades to the question it already had rather than grading a
#: mechanical engineer against the electrical menu.
_DECISION_MARGIN = 1.0


def _tokens(text: str) -> list[str]:
    out = []
    # The slash is a SEPARATOR, not a word character. Including it folded
    # "Welders/fabricators" into one token that no job title can ever match,
    # which silently cost eight of Part VI's role families their whole entry in
    # the vocabulary.
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9+#]*", str(text or "").lower()):
        word = raw.strip("+#")
        if len(word) >= 3:
            out.append(word)
    return out


#: Longest suffix two forms of one word may differ by. "engineer"/"engineering"
#: and "finance"/"financial" are one word; "general"/"generative" and
#: "account"/"accountability" are two, and conflating either pair sends a role
#: to the wrong department.
_MAX_SUFFIX = 3

#: Shared prefix at which two words with DIFFERENT suffixes are still one word.
#: Below it, "analyst" and "analytics" would collapse.
_MIN_SHARED = 6


def _same_word(left: str, right: str) -> bool:
    """Whether two words are the same word, allowing a suffix on either."""
    if left == right:
        return True
    shared = 0
    for a, b in zip(left, right):
        if a != b:
            break
        shared += 1
    if shared < _MIN_PREFIX:
        return False
    left_rest, right_rest = len(left) - shared, len(right) - shared
    if left_rest > _MAX_SUFFIX or right_rest > _MAX_SUFFIX:
        return False
    return shared >= _MIN_SHARED or left_rest == 0 or right_rest == 0


@lru_cache(maxsize=1)
def _seniority_words() -> frozenset[str]:
    """Words section 11.1 uses as SENIORITY BAND labels, not as functions.

    THE RUNBOOK KEEPS DEPARTMENT AND SENIORITY ON SEPARATE AXES and this
    resolver has to as well. Section 11.1 bands every department family by
    seniority, and its row labels are "Senior manager", "Director / VP",
    "Executive / Generalist", "Eng leadership", "CXO" and so on. Those words
    then reappear inside one department TITLE, "LEADERSHIP, GENERAL MANAGEMENT
    & EXECUTIVE", where they name a function rather than a rank.

    The collision is not hypothetical: in Indian job titles "Executive" is
    routinely a junior grade, and "Warehouse Executive" and "Executive
    Assistant" both resolved to the leadership department on that one word
    before this filter existed. Taken from the data rather than from a word list
    of this module's own, so it cannot drift from section 11.1.
    """
    families = layers.runbook_value("department_models", "baseline_weight_families")
    words: set[str] = set()
    for family in families.values():
        for band in (family.get("weights") or {}):
            words.update(_tokens(str(band)))
    return frozenset(words)


@lru_cache(maxsize=1)
def _vocabulary() -> dict[str, tuple[dict[str, float], frozenset[str]]]:
    """department -> (distinctive token -> weight, role-family phrases).

    Built once from Part VI itself, so the resolver has no word list of its own
    to drift from the document.
    """
    entries = _departments()
    weighted: dict[str, dict[str, float]] = {}
    phrases: dict[str, frozenset[str]] = {}
    for key, entry in entries.items():
        scores: dict[str, float] = {}

        def add(text: str, weight: float) -> None:
            for token in _tokens(text):
                if any(_same_word(token, word) for word in _seniority_words()):
                    continue
                scores[token] = max(scores.get(token, 0.0), weight)

        add(str(entry.get("title") or ""), _TITLE_WEIGHT)
        family_phrases: set[str] = set()
        for family in entry.get("role_families") or ():
            add(str(family), _ROLE_FAMILY_WEIGHT)
            for part in re.split(r"[/(),]", str(family)):
                cleaned = " ".join(part.split()).strip().lower()
                if len(cleaned) >= 4:
                    family_phrases.add(cleaned)
        for row in entry.get("competency_menu") or ():
            add(str(row.get("competency") or ""), _COMPETENCY_WEIGHT)
        weighted[str(key)] = scores
        phrases[str(key)] = frozenset(family_phrases)

    # Frequency is counted over WORDS THAT MATCH EACH OTHER, not over exact
    # strings, or "engineer" and "engineering" would each look rare while the
    # idea they share is in a third of the document.
    vocabulary = sorted({token for scores in weighted.values() for token in scores})
    frequency: dict[str, int] = {}
    for token in vocabulary:
        frequency[token] = sum(
            1 for scores in weighted.values()
            if any(_same_word(token, other) for other in scores)
        )
    return {
        key: (
            {t: w for t, w in scores.items()
             if frequency[t] <= _MAX_DEPARTMENT_FREQUENCY},
            phrases[key],
        )
        for key, scores in weighted.items()
    }


def resolve_department(*hints: str | None) -> str:
    """Which of the fifteen Part VI departments this role belongs to.

    Deterministic and pure: the same hints always produce the same department,
    which is what lets two candidates on one job be probed against the same
    graph. Raises `DepartmentUnmapped` when nothing matches, and when the best
    two are within `_DECISION_MARGIN` of each other -- restricting more where
    the higher authority is silent, because a coin toss between mechanical and
    electrical produces a plausible grade against the wrong menu and nothing
    downstream can detect it.
    """
    text = " ".join(str(hint) for hint in hints if hint and str(hint).strip())
    if not text.strip():
        raise DepartmentUnmapped(
            "No role title, department or job description was supplied, so no "
            "Part VI department model can be resolved."
        )
    lowered = " ".join(text.split()).lower()
    tokens = set(_tokens(lowered))
    vocabulary = _vocabulary()

    scores: dict[str, float] = {}
    for key in department_keys():
        token_weights, family_phrases = vocabulary[key]
        score = sum(
            weight for token, weight in token_weights.items()
            if any(_same_word(token, hint) for hint in tokens)
        )
        for phrase in family_phrases:
            if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(phrase), lowered):
                score += _ROLE_FAMILY_PHRASE_WEIGHT
        scores[key] = score

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score <= 0:
        raise DepartmentUnmapped(
            "%r matches none of Part VI's fifteen department models. Add one "
            "through the section 36 procedure rather than grading this role "
            "against a menu written for another function." % (text[:120],)
        )
    if best_score - runner_up < _DECISION_MARGIN:
        close = [key for key, value in ranked
                 if best_score - value < _DECISION_MARGIN]
        raise DepartmentUnmapped(
            "%r does not separate %s. A near tie is refused rather than "
            "broken: a plausible grade against the wrong department menu is "
            "undetectable downstream." % (text[:120], close)
        )
    return best


# -- Ordering the plan -------------------------------------------------------


def node_for_competency(
    competency: str, department_key: str
) -> EvidenceNode | None:
    """The node serving one competency, or None.

    None is a real answer, not a failure: a genuinely role-specific competency
    the Runbook menu has no row for has no node either, and the caller falls
    back to the matrix item's own observable-evidence statement, which Sutra's
    stage 2 guaranteed exists. Substituting the nearest menu row instead would
    relabel the item as something Part VI already knew about, which looks like
    traceability and is not.
    """
    wanted = " ".join(str(competency or "").split()).lower()
    if not wanted:
        return None
    nodes = nodes_for(department_key)
    for node in nodes:
        if node.competency_id.lower() == wanted or node.competency_key == wanted:
            return node
    for node in nodes:
        if node.name.lower() == wanted:
            return node
    # Last: the matrix item's words are a hiring manager's, so a menu row whose
    # every significant word appears in them is the same competency said
    # differently. Requiring EVERY word, not any, because "design" alone would
    # match four menu rows in most departments.
    for node in nodes:
        significant = [
            word for word in re.findall(r"[a-z0-9+#]{4,}", node.name.lower())
            if word not in {"and", "the", "with", "under"}
        ]
        if significant and all(word in wanted for word in significant):
            return node
    return None


# -- Triangulation: 38.1 groups and 38.3's gradient ---------------------------

#: 38.1 source 1. The candidate's own claim, and never its own corroboration.
SELF_WRITTEN_GROUP = "self_written"

#: Independence groups a ReadyPick assessment can actually produce today.
#: `self_structured` is the application's validation fields (38.1 source 2) and
#: `assessment` is the conversation itself (source 3).
#:
#: `artefact`, `live` and `third_party` are DELIBERATELY ABSENT. The platform
#: collects no work artefact and no reference, and 38.4 reserves "live" for a
#: synchronous session with a person. Claiming any of them would manufacture
#: corroboration, which is the one direction 5.4 says never to guess in.
REACHABLE_GROUPS: tuple[str, ...] = ("self_structured", "assessment")


@lru_cache(maxsize=1)
def independence_groups() -> tuple[str, ...]:
    """38.1's six independence groups, in the Runbook's own order."""
    rows = layers.runbook_value(
        "evidence_tiers", "independence", "groups", "sources"
    )
    if not isinstance(rows, list) or not rows:
        raise layers.RunbookDataUnavailable(
            "runbook_data/evidence_tiers.yaml has no independence groups. "
            "Section 38.1 states the six and they are not restated in code."
        )
    return tuple(str(row["group"]) for row in rows)


def corroboration_targets(
    node: EvidenceNode, *, available_sources: Iterable[str] | None = None
) -> tuple[list[str], list[str]]:
    """(reachable, out_of_band) independence groups for this node.

    The second list is what the platform CANNOT get. Returned rather than
    dropped, because it is what Miti reads as a reason to hold confidence down:
    a competency whose only real corroboration is a reference is a competency
    the assessment can probe and cannot confirm, and saying so is more honest
    than treating a well-argued answer as corroborated.
    """
    reachable = set(
        REACHABLE_GROUPS if available_sources is None else available_sources
    )
    inside = [group for group in node.corroborated_by if group in reachable]
    outside = [group for group in node.corroborated_by if group not in reachable]
    return inside, outside


@dataclass(frozen=True)
class SpecificityLevel:
    """One rung of 38.3's gradient."""

    level: int
    question: str
    answerable_by: str
    #: Whether 38.3 names this level a discriminator. Levels 4 and 5 are the
    #: ones a generative model produces generically and a participant produces
    #: specifically, which is the whole mechanism.
    discriminating: bool


@lru_cache(maxsize=1)
def specificity_levels() -> tuple[SpecificityLevel, ...]:
    """38.3's specificity gradient, read from the data.

    "A claim is probed at increasing specificity until either the candidate
    demonstrates participatory knowledge or the probe exhausts." That sentence
    is the conversation's whole triangulation posture, and it is also where the
    extension ceiling comes from: the gradient has a top, so probing one claim
    is provably finite.
    """
    entry = layers.runbook_value("evidence_tiers", "specificity_gradient")
    rows = entry.get("levels") if isinstance(entry, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise layers.RunbookDataUnavailable(
            "runbook_data/evidence_tiers.yaml has no specificity gradient. "
            "Section 38.3 states its five levels and they are not restated in "
            "code."
        )
    discriminators = {int(n) for n in entry.get("discriminators") or ()}
    return tuple(
        SpecificityLevel(
            level=int(row["level"]),
            # The Runbook sets levels 3 to 5 across two columns of a code block,
            # so the data marks those `_transcribed`. Either key is the same
            # rung.
            question=str(row.get("question") or row.get("question_transcribed") or ""),
            answerable_by=str(
                row.get("answerable_by") or row.get("answerable_by_transcribed") or ""
            ),
            discriminating=int(row["level"]) in discriminators,
        )
        for row in rows
    )


def discriminator_levels() -> tuple[int, ...]:
    """The gradient levels 38.3 calls the discriminators."""
    return tuple(
        level.level for level in specificity_levels() if level.discriminating
    )


def next_specificity_level(reached: int = 0) -> SpecificityLevel | None:
    """The next rung above `reached`, or None once the gradient exhausts.

    None is 38.3's own second stopping condition ("or the probe exhausts") and
    is what makes the conversation's extension provably finite.
    """
    for level in specificity_levels():
        if level.level > int(reached):
            return level
    return None


def minimum_discriminator_fraction() -> float:
    """38.3's design rule, as a fraction. "at least 40% of probe items must sit
    at Level 4 or 5", stated for all validation instruments across all
    departments."""
    return float(
        layers.runbook_value(
            "evidence_tiers", "specificity_gradient",
            "minimum_level_4_or_5_fraction",
        )
    )


def probe_level(*, ordinal: int, prior_substantive: int = 0) -> SpecificityLevel:
    """Which rung of 38.3's gradient this probe aims at. Deterministic.

    TWO RULES, BOTH FROM 38.3, AND THEY PULL AGAINST EACH OTHER.

      * "A claim is probed at increasing specificity" -- so a second probe of one
        item must sit above the first, which is what `prior_substantive` does.
      * "at least 40% of probe items must sit at Level 4 or 5" -- so a plan that
        asks one question per item cannot open every one of them at Level 1, or
        the instrument fails its own design rule before the candidate answers.

    The share is met EXACTLY rather than generously, and by position rather than
    by chance: probe `i` opens on a discriminator when the running count of
    discriminators owed passes a whole number. At 40% that is items 3, 5, 8, 10,
    13 and so on, which is a fifth of a twenty-question assessment plus a fifth
    again -- and which is IDENTICAL for two candidates on one job, because it is
    a function of the position in Sutra's plan and of nothing the candidate
    said. A random or model-chosen assignment would make two reports on one job
    incomparable, which is the property the fixed coverage plan exists to
    protect.

    Every other probe opens on the highest NON-discriminating rung, so a
    follow-up on it lands on a discriminator. Nothing here opens at Level 1:
    "What did you do?" is answerable by anyone, which 38.3 says in as many
    words, and the resume already answered it.
    """
    levels = specificity_levels()
    discriminators = [level for level in levels if level.discriminating]
    if not discriminators:
        raise layers.RunbookDataUnavailable(
            "runbook_data/evidence_tiers.yaml names no discriminating levels on "
            "38.3's gradient. Levels 4 and 5 are the discriminators and they "
            "are not restated in code."
        )
    fraction = minimum_discriminator_fraction()
    index = max(0, int(ordinal))
    # CEILING, NOT FLOOR, and the difference is the design rule itself. With a
    # floor the running count reaches int(N x 0.4) discriminators out of N,
    # which is BELOW 40% whenever the product is not a whole number: sixteen
    # questions would get six, and 6/16 is 0.375. "At least 40%" then fails on
    # exactly the interview lengths this product uses. Rounded before the
    # ceiling because 0.4 has no exact binary form and ceil(2.0000000000000004)
    # is 3.
    owed = _ceil(round((index + 1) * fraction, 9)) > _ceil(round(index * fraction, 9))
    if owed:
        floor = min(level.level for level in discriminators)
    else:
        below = [level.level for level in levels if not level.discriminating]
        floor = max(below) if below else min(level.level for level in discriminators)
    wanted = min(floor + max(0, int(prior_substantive)), levels[-1].level)
    for level in levels:
        if level.level == wanted:
            return level
    return levels[-1]


def extension_ceiling() -> int:
    """How many questions the conversation may add above Sutra's written plan.

    DERIVED, AND THE DERIVATION IS THE ARGUMENT. The Runbook states no
    conversation length anywhere: no question count, no probe count, no
    interview duration outside 21.5's twenty-minute walkthrough. What it does
    bound is how far ONE claim can be probed, and it bounds it exactly: 38.3's
    gradient has five rungs and probing stops "until either the candidate
    demonstrates participatory knowledge or the probe exhausts".

    Taking that same bound for the whole extension is the tightest reading the
    document supports, and the strict direction: five extra questions cannot
    turn a twenty-question assessment into a different instrument, and a
    conversation that still lacks evidence after climbing an entire gradient has
    established that the evidence is not there to be had. Reporting insufficient
    evidence at that point is the correct outcome and is never a rejection
    (6.7, and the architecture note's "never convert unresolved uncertainty into
    artificial confidence").

    SOURCE: RPN-PHIL-001 section 38.3 (v1.3). The gradient bounds how far ONE
    claim is probed; the Runbook now also bounds the SESSION, "by the number of
    specificity levels the gradient defines, applied across the session rather
    than per claim". An unbounded session is the same failure at a different
    scale: a candidate answering an ever-deepening sequence learns that
    thoroughness is punished. When the bound is reached with evidence still
    insufficient, the shortfall is reported as a shortfall and never converted
    into a low score.
    """
    return len(specificity_levels())


# -- The four classes of question --------------------------------------------
#
# From the architecture direction of 2026-08-28, section 6, which is ADVISORY
# and is the right model. The classes are not four prompts: they are four
# different reasons to ask, and the reason decides what a good answer would look
# like. Which class applies is DETERMINISTIC arithmetic over what the
# conversation already holds, for the same reason the coverage plan is: a class
# chosen by a model would vary between two candidates on one job and neither
# report could be compared with the other.

#: Verify a claim the candidate has already made. Climbs 38.3's gradient.
CLASS_CONFIRMATION = "confirmation"
#: Obtain evidence that is missing after the item was probed.
CLASS_GAP = "gap"
#: Resolve two readings that disagree. Never resolved by the conversation
#: itself; the point is to give the candidate the chance to explain (38.7,
#: "every contradiction gets a chance to be explained").
CLASS_CONTRADICTION = "contradiction"
#: Find capability the profile does not show.
#:
#: THIS IS THE CLASS THAT PROTECTS THE UNCONVENTIONAL CANDIDATE. Asking a gap
#: question of a silent profile establishes only that the profile is silent,
#: which is what an ATS already concluded; 6.6's Unknown discipline and axiom 7
#: ("absence of evidence is not evidence of absence") both say that is not a
#: finding. So a silent profile is asked what it has done, not asked to account
#: for what it lacks.
CLASS_DISCOVERY = "discovery"

QUESTION_CLASSES: tuple[str, ...] = (
    CLASS_CONFIRMATION,
    CLASS_GAP,
    CLASS_CONTRADICTION,
    CLASS_DISCOVERY,
)


def question_class(
    *,
    conflicting: bool = False,
    claim_present: bool = False,
    substantive_answers: int = 0,
    answers: int = 0,
) -> str:
    """Which of the four reasons applies to the next question on one item.

    Pure, total and ordered. Contradiction outranks everything for the reason
    `ledger.support_state` checks it first: an item with evidence on both sides
    is the most interesting one in the conversation and the easiest to lose
    behind a rule that lets support outweigh disagreement.

    `claim_present` means the candidate has ALREADY said something on this item,
    on the resume or earlier in the conversation. It is the difference between
    Gap and Discovery, and getting it backwards is what reproduces ATS bias.
    """
    if conflicting:
        return CLASS_CONTRADICTION
    if int(substantive_answers) > 0:
        return CLASS_CONFIRMATION
    if claim_present or int(answers) > 0:
        return CLASS_GAP
    return CLASS_DISCOVERY


# -- Sutra's routing ---------------------------------------------------------


async def required_evidence_sources(
    session: Any, job_id: Any, competency: str
) -> tuple[str, ...]:
    """The evidence sources Sutra's frozen matrix requires for one competency.

    LAZY IMPORT, AND A NAMED RAISE WHEN THE MATRIX CANNOT BE READ.
    `hiring/scorecard.py` is a parallel deliverable in this phase. Importing it
    at module scope would make this module unimportable until it lands, which
    would take the whole conversation down; returning an empty tuple on
    ImportError would report "no sources required" for every competency in the
    product, which is worse -- a silent fallback wearing a successful return
    value.

    So every reason the routing is unavailable is raised as
    `ScorecardUnavailable`, naming which one it was. The caller logs it and
    continues with the department graph, which is not a bank and still says what
    to establish; only the per-competency source routing is missing.

    GATE G1 IS NOT ENFORCED HERE AND MUST NOT BE. `require_frozen_matrix` IS the
    gate, and it is applied before a candidate reaches the conversation at all.
    A second enforcement path inside the question writer would be a copy of a
    rule, and the copy that got forgotten would be the one that mattered.
    """
    if session is None:
        raise ScorecardUnavailable(
            "No database session was supplied, so Sutra's frozen matrix cannot "
            "be read and the sources it flagged as required for %r are unknown."
            % (competency,)
        )
    try:
        from app.services.hiring import scorecard  # noqa: PLC0415
    except ImportError as exc:
        raise ScorecardUnavailable(
            "app.services.hiring.scorecard is not importable, so Sutra's frozen "
            "matrix cannot be read and the evidence sources it flagged as "
            "required for %r are unknown. Nothing is substituted for them."
            % (competency,)
        ) from exc
    try:
        matrix = await scorecard.require_frozen_matrix(session, job_id)
    except Exception as exc:  # noqa: BLE001
        # Includes `ScorecardNotFrozen`, which is G1 refusing. Re-raised under
        # this module's own class, with the original attached, so the caller
        # catches ONE thing and an operator still reads which one it was.
        raise ScorecardUnavailable(
            "Sutra's frozen matrix could not be read (%s), so the evidence "
            "sources it flagged as required for %r are unknown."
            % (type(exc).__name__, competency)
        ) from exc
    wanted = " ".join(str(competency or "").split()).lower()
    for item in getattr(matrix, "items", ()):
        if str(getattr(item, "competency", "")).strip().lower() == wanted:
            return tuple(str(s) for s in getattr(item, "evidence_sources", ()) or ())
    return ()

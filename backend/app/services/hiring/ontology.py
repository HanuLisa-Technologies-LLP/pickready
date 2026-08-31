"""The skills/competency ontology (spec-doc5 §A.3, Runbook §58).

    "so that vocabulary mismatch doesn't undervalue a candidate who describes
     'graph database' work as 'semantic technologies', or 'GD&T' as 'geometric
     tolerancing' -- pure vector similarity fails this and systematically
     disadvantages non-standard-vocabulary candidates, which the Runbook flags
     as a fairness issue, not just a quality one."

WHY THIS IS A FAIRNESS PROBLEM AND NOT A RANKING NIT
------------------------------------------------------
Vocabulary is not randomly distributed. Someone trained at a large Western
technology company says "observability" and "SRE"; someone who did the same work
at an Indian services firm says "application monitoring" and "production
support"; someone from academia says "semantic technologies" for what industry
calls "graph databases". A retrieval layer that scores these differently is not
measuring capability, it is measuring which vocabulary somebody was trained in --
which correlates with employer, with country, and with class.

Embeddings help and do not solve it. `voyage-context-4` will place "graph
database" and "semantic technologies" closer than chance, and it will still put
an exact lexical match above both, which is exactly the ranking that penalises
the person who said it differently.

SO THIS IS AN EXPLICIT TERM MAP, AND IT IS DATA
-------------------------------------------------
A curated equivalence table beats a model call here for the same reason the
planner is arithmetic: it is inspectable, it is diffable, it costs nothing at
request time, and it does not change its mind between two candidates on the same
job. A wrong entry is a line somebody can point at and delete.

EXPANSION IS SYMMETRIC AND IT IS ADDITIVE
-------------------------------------------
`expand` returns the query terms PLUS their equivalents; it never REPLACES a
term. That matters: replacing "GD&T" with "geometric tolerancing" would stop
matching the candidates who wrote "GD&T", which is the same failure pointed the
other way. The point is to stop vocabulary DECIDING, not to pick a winning
vocabulary.

WHAT THIS MUST NOT DO
----------------------
It is a RETRIEVAL and MATCHING aid. It must never expand a Must-have into
something the hiring manager did not ask for: a job requiring "Kubernetes" is
not satisfied by "container orchestration experience" in general, and Sutra's
matrix items are named from the department model precisely so that judgment
stays with a person. `expand` is used by Yukti's AI Score and by retrieval; it
is not used to decide whether a matrix item is met.

PROVENANCE, AND WHAT §58 DOES AND DOES NOT SETTLE
---------------------------------------------------
RPN-PHIL-001 §58 states the REQUIREMENT and gives three worked examples:

    "a skills/competency ontology is required so that vocabulary mismatch does
     not cause missed evidence -- 'graph database' and 'semantic technologies,'
     'GD&T' and 'geometric tolerancing,' 'FP&A' and 'business finance' must
     resolve to the same competency node. Pure vector similarity fails on this
     and will systematically undervalue candidates who describe their work in
     non-standard vocabulary, which correlates with non-standard backgrounds."

CONFIRMED: the requirement, the fairness argument, and two of the three
examples. §58 also confirms this module's placement, because its "what is never
retrieved into an evaluation" list is what keeps an ontology a retrieval aid
rather than a criterion.

CORRECTED: the third example was missing. "FP&A" sat in a group with
"financial planning and analysis", "budgeting and forecasting" and "planning and
analysis", and "business finance" was in none of them -- so the one pairing §58
names for the finance department did not resolve. That is the pairing most
likely to matter in India, where the same work is advertised as business finance
and described on a resume as FP&A, and the candidate who used the other word was
scoring a zero overlap on it.

STILL UNSPECIFIED: the table itself. §58 requires an ontology and does not
enumerate one, and nothing else in RPN-PHIL-001 does either. Every group below
other than the three §58 names is written to the same test: would a hiring
manager reading both terms agree they describe the same work? Marked
SOURCE: RPN-PHIL-001 §58 (v1.3) and recorded, because a curated equivalence table is a
fairness-relevant artefact and its entries should be reviewed rather than
assumed.
"""
from __future__ import annotations

import re
from typing import Iterable

__all__ = [
    "EQUIVALENCE_GROUPS",
    "expand",
    "canonical",
    "equivalent",
    "matches",
    "mentions",
    "normalise",
    "overlap",
]

#: Groups of terms that name the same work. Every term in a group expands to
#: every other. The FIRST term is the canonical one, used only for display and
#: never for filtering -- see `canonical`.
#:
#: Deliberately modest in size. A large auto-generated table would contain
#: near-misses ("data science" / "data engineering") that actively mislead, and
#: a near-miss here is worse than an absence: an absence costs a candidate a
#: little ranking, a near-miss credits them with something they did not do.
EQUIVALENCE_GROUPS: tuple[tuple[str, ...], ...] = (
    # SOURCE: RPN-PHIL-001 §58 (v1.3): §58 requires an ontology and names three
    # pairings; it does not enumerate a table. These three are the Runbook's
    # own and are used literally. Everything after them is this
    # implementation's, held to the same test, and is recorded for review in
    # RUNBOOK_OPEN_QUESTIONS_PHASE0B.md.
    ("graph database", "semantic technologies", "knowledge graph", "triple store", "rdf", "sparql"),
    ("gd&t", "geometric tolerancing", "geometric dimensioning and tolerancing"),
    # ── Engineering ──
    ("observability", "application monitoring", "apm", "production monitoring", "telemetry"),
    ("site reliability", "sre", "production support", "production engineering", "on-call engineering"),
    ("ci/cd", "continuous integration", "continuous delivery", "build pipeline", "release automation"),
    ("infrastructure as code", "iac", "terraform", "cloudformation", "config management"),
    ("container orchestration", "kubernetes", "k8s", "eks", "aks", "gke", "openshift"),
    ("message queue", "event streaming", "pub/sub", "kafka", "rabbitmq", "event bus"),
    ("relational database", "rdbms", "sql database", "postgres", "postgresql", "mysql", "oracle db"),
    ("nosql", "document database", "mongodb", "dynamodb", "cassandra"),
    ("vector search", "semantic search", "embedding search", "similarity search", "pgvector"),
    ("machine learning", "ml", "statistical modelling", "predictive modelling"),
    ("large language model", "llm", "generative ai", "genai", "foundation model"),
    ("retrieval augmented generation", "rag", "grounded generation"),
    ("microservices", "service oriented architecture", "soa", "distributed services"),
    ("api design", "rest api", "restful services", "web services", "graphql"),
    ("front end", "frontend", "ui development", "client side development"),
    ("back end", "backend", "server side development"),
    ("test automation", "automated testing", "qa automation", "sdet"),
    ("technical debt", "code health", "refactoring", "maintainability work"),
    ("incident management", "incident response", "outage handling", "major incident"),
    # ── Data ──
    ("data pipeline", "etl", "elt", "data ingestion", "data engineering"),
    ("data warehouse", "analytics warehouse", "olap", "dimensional model"),
    ("business intelligence", "bi", "reporting and dashboards", "mis reporting"),
    # ── Sales ──
    ("pipeline generation", "prospecting", "lead generation", "hunting", "new logo acquisition"),
    ("account management", "farming", "customer success", "relationship management"),
    ("solution selling", "consultative selling", "value selling", "needs based selling"),
    ("enterprise sales", "b2b sales", "corporate sales", "key account sales"),
    ("quota attainment", "target achievement", "revenue delivery", "number delivery"),
    # ── Finance ──
    # §58's third named pairing. "business finance" was absent before
    # reconciliation, so the one equivalence the Runbook names for finance did
    # not resolve -- and it is the pairing most likely to matter in this
    # product's primary market, where the same work is advertised as business
    # finance and written on a resume as FP&A.
    (
        "financial planning and analysis", "fp&a", "business finance",
        "budgeting and forecasting", "planning and analysis",
    ),
    ("internal controls", "sox", "control framework", "financial controls"),
    ("statutory reporting", "financial reporting", "annual accounts", "regulatory reporting"),
    ("month end close", "period close", "book closure", "closing the books"),
    ("business partnering", "commercial finance", "finance business partner", "decision support"),
    # ── Operations ──
    ("process improvement", "lean", "six sigma", "continuous improvement", "kaizen", "operational excellence"),
    ("supply chain", "logistics", "materials management", "distribution"),
    ("vendor management", "supplier management", "third party management", "procurement"),
    ("capacity planning", "resource planning", "workforce planning", "demand planning"),
    ("service level agreement", "sla management", "service delivery", "turnaround time"),
    # ── Leadership, across departments ──
    ("people management", "line management", "team leadership", "direct reports"),
    ("stakeholder management", "cross functional collaboration", "influence without authority"),
    ("change management", "transformation", "organisational change", "adoption"),
    ("mentoring", "coaching", "developing people", "talent development"),
    # -- Job-title conventions, Indian and non-Indian --------------------------
    #
    # ADDED FOR spec-doc6 4.4, which asks in as many words for "non-Indian AND
    # Indian job-title conventions". The groups above are mostly SKILL
    # vocabulary; a title is the other half of the same fairness problem, and in
    # this product's primary market it is the larger half. An Indian services
    # firm advertises for a "Deputy Manager, Business Finance" and a US company
    # advertises the identical job as an "FP&A Associate Manager"; the resumes
    # come back written in whichever vocabulary the candidate has lived in, and
    # a matcher that reads only one of them is measuring which country somebody
    # worked in.
    #
    # Held to the same test as every other group: would a hiring manager reading
    # both terms agree they describe the same WORK. Grade-ladder equivalences
    # that vary by company (a "Senior Manager" against a "Director") are
    # deliberately NOT here, because section 8.3 says title is context and scope
    # is the score input, and encoding a grade ladder would be doing the exact
    # normalisation section 8.3 refuses.
    ("entry level", "fresher", "graduate trainee", "campus hire", "new graduate", "trainee engineer"),
    ("software engineer", "software development engineer", "sde", "application developer",
     "programmer analyst", "software developer"),
    ("technical lead", "tech lead", "team lead", "module lead", "lead engineer"),
    ("associate manager", "assistant manager", "deputy manager"),
    ("project manager", "delivery manager", "engagement manager", "programme manager",
     "program manager"),
    ("quality assurance", "quality analyst", "test engineer", "qa engineer", "quality engineer"),
    ("technical support", "service desk", "help desk", "l1 support", "first line support"),
    ("business analyst", "functional consultant", "requirements analyst", "systems analyst"),
    ("data analyst", "mis executive", "reporting analyst", "analytics executive"),
    ("presales", "pre sales", "solution consulting", "sales engineering", "solution engineering"),
    ("site engineer", "field engineer", "execution engineer", "project engineer"),
    ("quantity surveying", "billing engineer", "bill of quantities", "boq", "quantity survey"),
    ("plant maintenance", "preventive maintenance", "breakdown maintenance", "upkeep"),
    ("tool design", "tool and die", "tooling", "die design", "jig and fixture design"),
    ("articleship", "audit internship", "accounting internship"),
    # -- Human resources ------------------------------------------------------
    ("talent acquisition", "recruitment", "hiring", "staffing", "sourcing"),
    ("industrial relations", "ir", "employee relations", "er"),
    ("learning and development", "l&d", "training and development", "capability building"),
    ("payroll", "compensation and benefits", "c&b", "salary administration"),
    ("statutory compliance", "labour compliance", "regulatory compliance", "labour law compliance"),
    # -- Vocabulary a non-standard-English resume reaches for -------------------
    #
    # spec-doc6 4.4 also asks for "candidates whose resumes are written in
    # non-standard English". Most of that is PHRASING rather than vocabulary and
    # is handled by the claim reader, which requires no particular English
    # construction to tier a line. What belongs HERE is the narrower case where
    # a different word is genuinely used for the same work: an engineer who
    # learnt the craft in an Indian services firm writes "requirement gathering"
    # and "production rollout" where another writes "requirements elicitation"
    # and "release".
    ("troubleshooting", "debugging", "issue resolution", "fault finding", "defect fixing"),
    ("requirement gathering", "requirements elicitation", "requirement analysis",
     "business requirement analysis"),
    ("release", "deployment", "go live", "production rollout", "implementation"),
    ("performance tuning", "performance optimisation", "performance optimization",
     "latency optimisation", "latency optimization"),
    ("code review", "peer review", "code walkthrough"),
    ("technical documentation", "technical writing", "sop preparation", "runbook authoring"),
    ("client servicing", "client handling", "customer handling", "client management"),
    ("root cause analysis", "rca", "problem management", "defect analysis"),
    ("cost optimisation", "cost optimization", "cost reduction", "cost saving",
     "spend rationalisation", "spend rationalization"),
    ("automation", "process automation", "scripting", "workflow automation"),
)

_WS = re.compile(r"[\s_\-/]+")


def normalise(term: str) -> str:
    """Lowercase, collapse separators, strip. The comparison form.

    Separators are collapsed rather than removed so "ci/cd", "ci-cd" and "ci cd"
    all agree, while "cicd" -- which nobody writes -- is not manufactured.
    """
    return _WS.sub(" ", (term or "").strip().lower())


def _build_index() -> dict[str, frozenset[str]]:
    index: dict[str, frozenset[str]] = {}
    for group in EQUIVALENCE_GROUPS:
        members = frozenset(normalise(term) for term in group if term.strip())
        for member in members:
            # A term appearing in two groups gets the UNION rather than the last
            # write. "kubernetes" belongs with container orchestration and could
            # reasonably appear elsewhere; silently taking one group would make
            # expansion depend on table order.
            index[member] = index.get(member, frozenset()) | members
    return index


_INDEX: dict[str, frozenset[str]] = _build_index()

_CANONICAL: dict[str, str] = {
    normalise(term): group[0]
    for group in EQUIVALENCE_GROUPS
    for term in group
}


def equivalent(term: str) -> frozenset[str]:
    """Every term that names the same work, including `term` itself.

    Returns a set containing only the input when nothing is known, rather than
    an empty set. A caller unioning results should never have a term VANISH
    because the ontology had not heard of it -- an unknown term is a term that
    stands on its own, not one that stops existing.
    """
    key = normalise(term)
    if not key:
        return frozenset()
    return _INDEX.get(key, frozenset({key}))


def canonical(term: str) -> str:
    """The group's first term. FOR DISPLAY ONLY.

    Never for filtering or for storage. Canonicalising on the way IN would
    rewrite what a candidate actually wrote, and this codebase's standing rule
    is that an answer is never re-worded -- a summary of an answer is not
    evidence of what someone said. The same logic applies to a skill term.
    """
    return _CANONICAL.get(normalise(term), normalise(term))


def expand(terms: Iterable[str]) -> list[str]:
    """The input terms PLUS their equivalents, de-duplicated, order-stable.

    ADDITIVE, never substitutive. Replacing "GD&T" with "geometric tolerancing"
    would stop matching the candidates who wrote "GD&T", which is the identical
    failure pointed the other way. The point is to stop vocabulary from
    deciding, not to pick a winning vocabulary.

    Order is input-first, then equivalents, so a caller that truncates keeps
    what was actually asked for.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    originals = [normalise(t) for t in terms if normalise(t)]
    for term in originals:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    for term in originals:
        for sibling in sorted(equivalent(term)):
            if sibling not in seen:
                seen.add(sibling)
                ordered.append(sibling)
    return ordered


#: Words too common in requirement and resume prose to carry any signal on
#: their own. Deliberately short: an aggressive stop list is a second, invisible
#: vocabulary filter, and the whole point of this module is that the product
#: does not get to decide which words count.
_NOISE = frozenset(
    """
    a an and are as at be by for from has have in is it its of on or that the to
    with will you your our we they this these those been being do does not was
    were and/or via per over under across using use used new strong good
    excellent ability able skills skill knowledge experience experienced work
    working role job team teams company year years
    """.split()
)

#: Every known term, longest first, so "geometric dimensioning and tolerancing"
#: is found before "geometric tolerancing" would be and a shorter term cannot
#: shadow the longer one it is a prefix of.
_TERMS_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(_INDEX, key=lambda t: (-len(t), t))
)

_BOUNDARY = re.compile(r"[a-z0-9]")


def _tokens(text: str) -> list[str]:
    """Normalised words, with the noise words dropped."""
    return [w for w in normalise(text).split(" ") if w and w not in _NOISE]


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Is `needle` present in `haystack` at token boundaries?

    Plain `in` is wrong here in one direction that matters: "rag" is a term in
    this table and it is a substring of "storage", "fragment" and "average". A
    boundary check is the difference between finding retrieval-augmented
    generation on a resume and finding it in the word "storage".
    """
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        before = haystack[index - 1] if index > 0 else " "
        after_index = index + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else " "
        if not _BOUNDARY.match(before) and not _BOUNDARY.match(after):
            return True
        start = index + 1


def mentions(text: str) -> frozenset[str]:
    """Every term in this table that `text` actually contains.

    Phrase-aware, which is the property a bare word-set intersection does not
    have: "we replaced the semantic technologies layer" contains the two-word
    term "semantic technologies", and a set of single words does not, so a
    requirement for "graph database" would have missed it. That miss is exactly
    the fairness failure section 58 names, arriving through tokenisation rather
    than through vocabulary.
    """
    haystack = normalise(text)
    if not haystack:
        return frozenset()
    return frozenset(
        term for term in _TERMS_BY_LENGTH if _contains_phrase(haystack, term)
    )


def matches(requirement: str, text: str) -> bool:
    """Does `text` evidence `requirement`, ONCE VOCABULARY IS SET ASIDE?

    THIS IS THE FUNCTION THE PRE-SCREEN ASKS, and it is deliberately not the
    function a matrix item asks. This module's standing rule is that expansion
    must never decide whether a Must-have is MET, because a job requiring
    Kubernetes is not satisfied by "container orchestration" in the abstract and
    that judgement belongs to a person. A pre-screen grade is a different
    question: it is a triage reading of a document, and there the failure mode
    runs the other way, because a candidate who wrote the other word for the
    same work drops out of the list before any person sees them.

    Two ways to match, in order:

      1. The requirement, or any term this table calls equivalent to it, appears
         in the text at token boundaries. This is what carries "semantic
         technologies" against a requirement for "graph database", and it is
         symmetric, because equivalence is.
      2. Failing that, EVERY significant word of the requirement is present,
         each satisfiable by its own equivalents. "Stakeholder management" needs
         both halves; it is not met by a resume that only says "management".

    Rule 2 is `all` rather than `any` on purpose. `any` would let one common
    word carry a whole multi-word requirement, and a matcher that says yes to
    everything is not fairer than one that says no to everything, it is just
    wrong in the direction that is harder to notice.
    """
    haystack = normalise(text)
    requirement_key = normalise(requirement)
    if not haystack or not requirement_key:
        return False

    for sibling in equivalent(requirement_key):
        if _contains_phrase(haystack, sibling):
            return True

    words = _tokens(requirement_key)
    if not words:
        return False
    for word in words:
        if not any(_contains_phrase(haystack, sibling) for sibling in equivalent(word)):
            return False
    return True


def overlap(left: Iterable[str], right: Iterable[str]) -> frozenset[str]:
    """Terms the two sides share ONCE VOCABULARY IS SET ASIDE.

    This is the function Yukti's AI Score should use in place of a raw set
    intersection. A candidate who wrote "semantic technologies" against a JD
    asking for "graph database" currently scores zero overlap on that skill;
    here they score one, which is the correct answer, and it is the difference
    between measuring capability and measuring which words somebody was trained
    to use.
    """
    left_expanded = {
        canonical(term) for term in expand(left)
    }
    right_expanded = {
        canonical(term) for term in expand(right)
    }
    return frozenset(left_expanded & right_expanded)

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
RUNBOOK-AMBIGUITY (§58) and recorded, because a curated equivalence table is a
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
    "normalise",
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
    # RUNBOOK-AMBIGUITY (§58): §58 requires an ontology and names three
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

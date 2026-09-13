"""Retrieval is a RANKING PRIOR. Nothing that scores may import it (W6.5).

Modelled on `tests/test_proctoring_scoring_isolation.py`, which is the file
this codebase already uses to keep one subsystem out of another's import graph.

THE RULE
--------
"A candidate linked to a job is always scored, and retrieval is a ranking prior
only." Retrieval decides which evidence an agent reads FIRST. It must never
decide what that evidence is WORTH, and it must never decide who gets scored.

The standing rule underneath it does not change and is what makes this
boundary matter: INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE. A dimension
with too little behind it is excluded from the composite and paid for in
CONFIDENCE, never scored low, so that a career changer gets a low-confidence
report that goes to a human rather than a confidently poor grade that does not.
If a retrieval signal -- a fused rank, a rerank score, a sufficiency verdict --
reached the aggregator, "we found less about this person" would become "this
person is worse", silently and with no way to tell the two apart afterwards.

W6 makes this urgent rather than theoretical. Until now `services/rag` produced
an ORDER and nothing else. It now also produces a relevance score from a
cross-encoder and, in W6.5, a sufficiency judgement -- both of which look
exactly like numbers a scorer could use.

TWO DIRECTIONS, AND BOTH ARE NEEDED
------------------------------------
Outward: the four modules W6.5 names must not import `services.rag`. Those are
the ones that turn bands into a grade and a grade into a word.

Inward: `services/rag` must not import a scorer. That is the half that is easy
to skip and the one that holds the line, because the tempting change is not
"let the aggregator read the rerank score", it is "let the reranker prefer the
dimension the candidate scored well on", and once the import exists the
dependency runs both ways in review.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
PACKAGE = APP / "services" / "rag"

RAG_MODULE = "app.services.rag"

#: The four modules W6.5 names, each with what it does that makes retrieval
#: unwelcome in it. Paths rather than module names, so a failure names the file.
FORBIDDEN_IMPORTERS: dict[str, str] = {
    "services/miti/aggregation.py": (
        "turns five dimension bands into the grade a client reads, and imports "
        "no router at all"
    ),
    "services/miti/caps.py": "applies the Must-have cap to the composite score",
    "services/rating.py": "the ONE rating scale, four grades, 90/75/60",
    "services/tiers.py": "a thin alias over rating; it has no arithmetic left",
}

#: Packages `services/rag` must never reach into. Every one of them decides,
#: weights or orders something about a candidate.
FORBIDDEN_TARGETS = (
    "app.services.miti",
    "app.services.siddhi",
    "app.services.rating",
    "app.services.tiers",
    "app.services.matching",
    "app.services.functional_assessment",
    "app.services.ppi",
    "app.services.job_candidates",
    "app.services.job_relevance",
    "app.services.gap_analysis",
    "app.services.dashboard",
    "app.services.evidence",
)


def _imports(path: pathlib.Path) -> set[str]:
    """Every module this file imports, at module scope or inside a function.

    Deliberately includes deferred imports. An aggregator that reached for the
    reranker inside a function would be exactly as coupled as one that imported
    it at the top, and rather harder to notice.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


# ── The outward direction ────────────────────────────────────────────────────


@pytest.mark.parametrize("relative", sorted(FORBIDDEN_IMPORTERS), ids=lambda p: p)
def test_no_scoring_module_imports_retrieval(relative: str) -> None:
    path = APP / relative
    offenders = sorted(
        name for name in _imports(path) if name.startswith(RAG_MODULE)
    )
    assert not offenders, (
        f"{relative} ({FORBIDDEN_IMPORTERS[relative]}) imports retrieval: "
        f"{offenders}. Retrieval is a ranking prior only. Insufficient evidence "
        "is excluded from the composite and paid for in confidence, never "
        "scored low."
    )


def test_the_four_named_modules_all_still_exist() -> None:
    """A stale list is how a ratchet stops ratcheting: the next real offender
    happens to be a file that was renamed and the check passes on nothing."""
    missing = [name for name in FORBIDDEN_IMPORTERS if not (APP / name).exists()]
    assert not missing, missing


def test_the_aggregator_reaches_no_ranking_machinery_at_all() -> None:
    """Named separately because this is THE file the rule exists about: it is
    the deterministic step that turns five bands into a delivered grade, and it
    already imports no router. A rerank score is a number, and a number in this
    module is the one thing that could make two runs over identical evidence
    produce different grades."""
    imports = _imports(APP / "services" / "miti" / "aggregation.py")
    for banned in ("app.services.rag", "app.services.llm_router", "voyageai"):
        assert not any(name.startswith(banned) for name in imports), banned


# ── The inward direction ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_retrieval_imports_nothing_that_scores(path: pathlib.Path) -> None:
    """The tempting change is not "let the aggregator read the rerank score",
    it is "let the reranker prefer the dimension the candidate scored well
    on"."""
    offenders = sorted(
        name
        for name in _imports(path)
        if any(name.startswith(target) for target in FORBIDDEN_TARGETS)
    )
    assert not offenders, f"{path.name} imports a scoring module: {offenders}"


def test_the_sweep_reads_the_whole_rag_package() -> None:
    """A guard on the guard. An empty glob would make every parametrised case
    above vanish and the file would report green having asserted nothing."""
    names = {path.name for path in PACKAGE.glob("*.py")}
    for expected in ("retrieval.py", "reranker.py", "contextual.py", "index.py"):
        assert expected in names, names


def test_the_import_detector_sees_a_deferred_import(tmp_path: pathlib.Path) -> None:
    """An import inside a function must count, or the sweep misses the easiest
    way to couple the two."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def aggregate():\n"
        "    from app.services.rag import reranker\n"
        "    return reranker\n",
        encoding="utf-8",
    )
    assert any(name.startswith(RAG_MODULE) for name in _imports(sample))


# ── The shape of the signal, not only who holds it ───────────────────────────


def test_a_retrieved_chunk_carries_no_grade_band_or_weight() -> None:
    """Enforcement by SHAPE as well as by import, the way `EvaluatorInput` and
    `JobFacts` do it. `score` is a fused RANK value and says so in its own
    docstring; there is no field a band, a weight or a level could live in."""
    from app.services.rag.retrieval import RetrievedChunk

    fields = set(RetrievedChunk.__dataclass_fields__)
    for banned in ("grade", "band", "weight", "level", "rating", "tier", "verdict"):
        assert not any(banned in name for name in fields), (banned, fields)


def test_the_rerank_outcome_carries_no_relevance_number_out_of_the_module() -> None:
    """The cross-encoder returns a relevance score per document. It is used to
    ORDER and then dropped: a number that left this module would be a number a
    scorer could reach, and the vendor's relevance is not on any scale this
    product grades against."""
    from app.services.rag.reranker import RerankOutcome

    fields = set(RerankOutcome.__dataclass_fields__)
    assert fields == {"chunks", "reranker", "degraded", "reason"}, fields


def test_the_run_record_is_words_and_a_boolean_and_nothing_else() -> None:
    """`as_dict` is what a run record and an operator view read. It reports
    which reranker ran and whether it degraded. No score, no count, no
    candidate."""
    from app.services.rag.reranker import RerankOutcome

    record = RerankOutcome(chunks=[], reranker="lexical").as_dict()
    assert set(record) == {"reranker", "degraded", "reason"}
    assert isinstance(record["degraded"], bool)
    assert isinstance(record["reranker"], str)

"""Citation SUPPORT, not citation existence (PLAN-p5 P5-D9).

`citations` guarantees a statement cites a ref that exists. These tests pin the
check that the cited text actually SUPPORTS the statement: deterministic first
(a shared content term, and no invented proper noun), semantic second (a
voyage-4 cosine at or above `siddhi_support_similarity_min`) only when the
anchor fails, and an honest `weak` when the semantic tier cannot run.

The embedder is a stub throughout: a test that called the vendor would measure
the vendor. The stub maps texts to fixed vectors so a paraphrase and an
unrelated sentence are distinguishable by construction.
"""
from __future__ import annotations

import pytest

from app.services.embeddings import EmbeddingError
from app.services.siddhi import support

ANSWER = (
    "I moved the orders service onto the new cluster over two sprints and "
    "wrote the rollback runbook myself."
)


def _embedder(table: dict[str, list[float]], calls: list[list[str]] | None = None):
    async def _embed(texts: list[str]) -> list[list[float]]:
        if calls is not None:
            calls.append(list(texts))
        return [table.get(text, [0.0, 0.0, 1.0]) for text in texts]

    return _embed


@pytest.mark.asyncio
async def test_a_statement_sharing_a_content_term_with_its_answer_is_supported() -> None:
    verdict = await support.assess(
        "They migrated the orders service and owned the rollback runbook.",
        [ANSWER],
        subject_terms=("Distributed Systems",),
        embed=None,
    )
    assert verdict.level == support.LEVEL_SUPPORTED
    assert verdict.reason == support.REASON_ANCHORED


@pytest.mark.asyncio
async def test_an_invented_proper_noun_is_unsupported_whatever_else_matches() -> None:
    """Sharing "orders" with the answer does not rescue a sentence that names a
    technology the candidate never mentioned: the invented term is decisive."""
    verdict = await support.assess(
        "They moved the orders service onto Kubernetes with Terraform.",
        [ANSWER],
        subject_terms=("Distributed Systems",),
        embed=None,
    )
    assert verdict.level == support.LEVEL_UNSUPPORTED
    assert verdict.reason == support.REASON_INVENTED


def test_the_skill_name_is_neither_an_anchor_nor_an_invention() -> None:
    """Naming the skill being assessed proves nothing about the answer, so it
    cannot anchor a statement, and it is never an invention either: the
    deterministic tier leaves it undecided for the semantic one."""
    verdict = support.deterministic(
        "Distributed Systems capability was stated.",
        [ANSWER],
        subject_terms=("Distributed Systems",),
    )
    assert verdict is None


def test_report_vocabulary_is_not_an_anchor() -> None:
    """"The candidate described an outcome" shares words with almost any
    answer and proves nothing about this one."""
    assert (
        support.deterministic(
            "The candidate described a specific outcome and personal actions.",
            ["The outcome was that my personal actions helped the candidate pool."],
            subject_terms=(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_paraphrase_is_rescued_by_the_semantic_tier() -> None:
    statement = "They shifted a live system to fresh infrastructure."
    calls: list[list[str]] = []
    embed = _embedder({statement: [1.0, 0.0, 0.0], ANSWER: [0.9, 0.1, 0.0]}, calls)
    verdict = await support.assess(
        statement, [ANSWER], subject_terms=(), embed=embed, threshold=0.55
    )
    assert verdict.level == support.LEVEL_SUPPORTED
    assert verdict.reason == support.REASON_SEMANTIC_MATCH
    assert calls, "the semantic tier ran"


@pytest.mark.asyncio
async def test_an_unrelated_sentence_is_unsupported_by_the_semantic_tier() -> None:
    statement = "They negotiated a vendor contract under budget pressure."
    embed = _embedder({statement: [0.0, 1.0, 0.0], ANSWER: [1.0, 0.0, 0.0]})
    verdict = await support.assess(
        statement, [ANSWER], subject_terms=(), embed=embed, threshold=0.55
    )
    assert verdict.level == support.LEVEL_UNSUPPORTED
    assert verdict.reason == support.REASON_SEMANTIC_BELOW


@pytest.mark.asyncio
async def test_the_threshold_comes_from_settings_when_not_given(monkeypatch) -> None:
    from app.core.config import get_settings

    statement = "They shifted a live system to fresh infrastructure."
    embed = _embedder({statement: [1.0, 0.0, 0.0], ANSWER: [0.6, 0.8, 0.0]})
    # cosine 0.6: supported at the default 0.55, unsupported at 0.65.
    assert get_settings().siddhi_support_similarity_min == 0.55
    verdict = await support.assess(statement, [ANSWER], embed=embed)
    assert verdict.level == support.LEVEL_SUPPORTED
    monkeypatch.setattr(get_settings(), "siddhi_support_similarity_min", 0.65)
    verdict = await support.assess(statement, [ANSWER], embed=embed)
    assert verdict.level == support.LEVEL_UNSUPPORTED


@pytest.mark.asyncio
async def test_no_semantic_tier_is_weak_with_the_reason_never_supported() -> None:
    """"We could not check" is not "it checked out"."""
    verdict = await support.assess(
        "They shifted a live system to fresh infrastructure.",
        [ANSWER],
        embed=None,
    )
    assert verdict.level == support.LEVEL_WEAK
    assert verdict.reason == support.REASON_SEMANTIC_UNAVAILABLE


@pytest.mark.asyncio
async def test_an_embedding_failure_is_weak_and_logged_by_class_name(caplog) -> None:
    async def _down(texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("Voyage endpoint failure: ConnectError")

    with caplog.at_level("WARNING"):
        verdict = await support.assess(
            "They shifted a live system to fresh infrastructure.",
            [ANSWER],
            embed=_down,
        )
    assert verdict.level == support.LEVEL_WEAK
    assert verdict.reason == support.REASON_SEMANTIC_UNAVAILABLE
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "EmbeddingError" in logged
    assert "orders service" not in logged


@pytest.mark.asyncio
async def test_a_statement_resting_only_on_the_search_record_is_weak() -> None:
    verdict = await support.assess("No answer addressed this area.", [], embed=None)
    assert verdict.level == support.LEVEL_WEAK
    assert verdict.reason == support.REASON_SEARCHED_ONLY


@pytest.mark.asyncio
async def test_many_statements_share_one_embedding_call() -> None:
    first = "They shifted a live system to fresh infrastructure."
    second = "They negotiated a vendor contract under budget pressure."
    calls: list[list[str]] = []
    embed = _embedder(
        {first: [1.0, 0.0, 0.0], second: [0.0, 1.0, 0.0], ANSWER: [1.0, 0.0, 0.0]},
        calls,
    )
    verdicts = await support.assess_many(
        [
            support.SupportRequest(key="a", statement=first, excerpts=(ANSWER,)),
            support.SupportRequest(key="b", statement=second, excerpts=(ANSWER,)),
        ],
        embed=embed,
        threshold=0.55,
    )
    assert len(calls) == 1
    assert calls[0].count(ANSWER) == 1, "a shared excerpt is embedded once"
    assert verdicts["a"].level == support.LEVEL_SUPPORTED
    assert verdicts["b"].level == support.LEVEL_UNSUPPORTED


def test_the_production_embedder_is_absent_without_a_real_model(monkeypatch) -> None:
    """Outside production `embed` returns pseudo-random vectors with no key,
    and a verdict decided by a random cosine would look exactly like a real
    one. So no model configured means no semantic tier."""
    from app.services import embeddings

    monkeypatch.setattr(embeddings, "is_semantic", lambda: False)
    assert support.semantic_embedder() is None
    monkeypatch.setattr(embeddings, "is_semantic", lambda: True)
    assert support.semantic_embedder() is embeddings.embed


def test_the_verdict_carries_no_number_and_its_notes_are_words() -> None:
    import re

    verdict = support.SupportVerdict(support.LEVEL_UNSUPPORTED, support.REASON_SEMANTIC_BELOW)
    assert verdict.as_dict() == {"level": "unsupported", "reason": "semantic_below_threshold"}
    for note in support.SUPPORT_NOTES.values():
        assert note
        assert not re.search(r"\d", note)
        assert chr(8212) not in note


def test_only_an_unsupported_or_misattributed_statement_carries_a_marker() -> None:
    """A marker on every sound sentence, or on every sentence an outage left
    unchecked, is noise that teaches a reader to skip the one that matters."""
    assert support.note_for(support.LEVEL_SUPPORTED, support.REASON_ANCHORED) is None
    for reason in (
        support.REASON_SEMANTIC_UNAVAILABLE,
        support.REASON_SEARCHED_ONLY,
    ):
        assert support.note_for(support.LEVEL_WEAK, reason) is None
    assert support.note_for(support.LEVEL_UNSUPPORTED, support.REASON_INVENTED) == (
        support.SUPPORT_NOTES[support.LEVEL_UNSUPPORTED]
    )
    assert support.note_for(support.LEVEL_WEAK, support.REASON_ELSEWHERE) == (
        support.SUPPORT_NOTES[support.REASON_ELSEWHERE]
    )
    assert support.note_for(None, None) is None


@pytest.mark.parametrize("value", [0.0, -0.2, 1.01, 55.0])
def test_a_threshold_outside_the_cosine_range_refuses_to_boot(value) -> None:
    """Above one nothing reaches it (every paraphrase unsupported, every report
    to review); at or below zero every unrelated sentence is supported. Both
    would read as the check working, so the setting is refused at load. 55.0
    is the percentage somebody would type meaning 0.55."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="SIDDHI_SUPPORT_SIMILARITY_MIN"):
        Settings(siddhi_support_similarity_min=value)


@pytest.mark.parametrize("value", [0.01, 0.55, 1.0])
def test_a_threshold_inside_the_cosine_range_loads(value) -> None:
    from app.core.config import Settings

    assert Settings(siddhi_support_similarity_min=value).siddhi_support_similarity_min == value

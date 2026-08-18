"""The context engine: chunking, fusion, reranking, assembly.

The database halves (indexing and the two SQL retrievers) need Postgres and are
exercised by the deployed smoke path. What is asserted here is everything that
decides WHAT an agent ends up reading, because those are the decisions that
silently change a grade:

  * a chunk is a unit of meaning, not a fixed number of characters;
  * fusion reads rank order only, because a cosine distance and a ts_rank are
    not on the same scale;
  * assembly drops whole chunks and says so, rather than cutting a sentence in
    half and letting a model complete it from its own priors.
"""
from __future__ import annotations

import uuid

from app.services.rag import chunking, context, retrieval

# ── chunking ─────────────────────────────────────────────────────────────────

_RESUME = """
EXPERIENCE

Senior Engineer, Northwind Data. Ran the streaming ingestion platform on Kafka
across three regions, owning partition strategy and the consumer group rebalance
during a migration from a single cluster. Cut replay time for the nightly
reconciliation job from hours to minutes by repartitioning on tenant.

Engineer, Halcyon. Built the reporting warehouse on Postgres, then moved it to
Iceberg when the table count passed a thousand and vacuum stopped keeping up.

EDUCATION

B.Tech, Computer Science.

SKILLS

Kafka, Postgres, Iceberg, Terraform, Python
"""


def test_a_chunk_is_a_unit_of_meaning_not_a_fixed_width() -> None:
    """A heading always arrives attached to the text beneath it.

    A heading alone matches its own words and carries no evidence, and it is
    what a naive splitter produces most of. Note the education entry: it is
    shorter than MIN_CHARS and is still its own chunk, because the block after
    it belongs to a different section and merging across that boundary would
    file a degree under Skills.
    """
    chunks = chunking.chunk_resume(_RESUME)
    assert chunks
    for chunk in chunks:
        assert chunk.content.strip() not in {"EXPERIENCE", "EDUCATION", "SKILLS"}
        assert len(chunk.content.split()) > 1


def test_section_types_are_inferred_from_the_documents_own_headings() -> None:
    sections = {chunk.section_type for chunk in chunking.chunk_resume(_RESUME)}
    assert chunking.SECTION_EXPERIENCE in sections
    assert chunking.SECTION_SKILLS in sections


def test_markdown_headings_classify_a_jd() -> None:
    jd = (
        "# Staff Engineer\n\nWe are hiring.\n\n"
        "## Responsibilities\n\nOwn the ingestion platform end to end and lead "
        "the migration off the single cluster. Set the partition strategy.\n\n"
        "## Required skills\n\nKafka, Postgres, Terraform and a working "
        "knowledge of distributed consensus in production systems.\n"
    )
    sections = {chunk.section_type for chunk in chunking.chunk_jd(jd)}
    assert chunking.SECTION_RESPONSIBILITIES in sections
    assert chunking.SECTION_SKILLS in sections


def test_an_oversized_block_splits_on_sentences_not_mid_word() -> None:
    block = " ".join(
        f"Sentence number {index} describes the ingestion platform in detail."
        for index in range(60)
    )
    chunks = chunking.chunk_text(block, source_type=chunking.SOURCE_RESUME)
    assert len(chunks) > 1
    for chunk in chunks:
        assert not chunk.content.endswith(" Sentenc")
        assert chunk.content.strip()


def test_a_single_sentence_longer_than_the_target_is_kept_whole() -> None:
    """Hard wrapping is worse: retrieval would return half a sentence."""
    monster = "The platform " + "and the ingestion pipeline " * 60 + "shipped."
    chunks = chunking.chunk_text(monster, source_type=chunking.SOURCE_RESUME)
    assert len(chunks) == 1
    assert chunks[0].content == monster.strip()


def test_a_question_and_its_answer_are_never_split_apart() -> None:
    """A question without its answer proves nothing; an answer without its
    question is a paragraph with no referent."""
    chunks = chunking.chunk_exchanges(
        [
            {"question": "How did you handle the rebalance?", "answer": "We drained " + "slowly " * 200},
            {"question": "Unanswered?", "answer": ""},
        ]
    )
    assert len(chunks) == 1
    assert chunks[0].content.startswith("Q: How did you handle")
    assert "A: We drained" in chunks[0].content
    assert chunks[0].section_type == chunking.SECTION_QA


def test_chunk_hashes_make_re_embedding_incremental() -> None:
    first = chunking.chunk_resume(_RESUME)
    again = chunking.chunk_resume(_RESUME)
    assert [c.content_sha256 for c in first] == [c.content_sha256 for c in again]

    edited = chunking.chunk_resume(_RESUME.replace("Iceberg", "Delta Lake"))
    changed = sum(
        1
        for old, new in zip(first, edited)
        if old.content_sha256 != new.content_sha256
    )
    assert 0 < changed < len(first), "an edit must not invalidate the whole document"


def test_a_source_version_changes_when_the_document_does() -> None:
    assert chunking.source_version(_RESUME) == chunking.source_version(_RESUME)
    # Whitespace-only differences are the same document.
    assert chunking.source_version(_RESUME) == chunking.source_version(
        _RESUME.replace("\n\n", "\n \n")
    )
    assert chunking.source_version(_RESUME) != chunking.source_version(
        _RESUME + "\nExtra line."
    )


# ── fusion ───────────────────────────────────────────────────────────────────


def _ids(count: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=index + 1) for index in range(count)]


def test_fusion_reads_rank_order_only() -> None:
    """A cosine distance and a ts_rank are not on the same scale, so any fixed
    weighting between them is a number nobody can justify."""
    a, b, c = _ids(3)
    fused = retrieval.fuse({"semantic": [a, b], "keyword": [c, a]})
    # `a` is first in one list and second in the other; it must beat both of the
    # chunks that only one retriever found.
    assert fused[a][0] > fused[b][0]
    assert fused[a][0] > fused[c][0]
    assert set(fused[a][1]) == {"semantic", "keyword"}


def test_a_chunk_only_one_retriever_found_still_survives_fusion() -> None:
    """Dropping single-retriever hits would make the two halves agree by
    construction rather than by evidence."""
    a, b = _ids(2)
    fused = retrieval.fuse({"semantic": [a], "keyword": [b]})
    assert set(fused) == {a, b}


def test_fusion_of_nothing_is_nothing_rather_than_an_error() -> None:
    assert retrieval.fuse({"semantic": [], "keyword": []}) == {}


# ── reranking ────────────────────────────────────────────────────────────────


def _chunk(content: str, section: str = chunking.SECTION_EXPERIENCE, score: float = 0.1):
    return retrieval.RetrievedChunk(
        chunk_id=uuid.uuid4(),
        content=content,
        source_type=chunking.SOURCE_RESUME,
        source_id=uuid.uuid4(),
        section_type=section,
        ordinal=0,
        score=score,
    )


def test_evidence_outranks_a_skills_list_mentioning_the_same_word() -> None:
    """A skills list matching a skills query proves nothing that its existence
    did not already prove."""
    evidence = _chunk(
        "Owned the Kafka partition strategy and ran the consumer group rebalance.",
        chunking.SECTION_EXPERIENCE,
    )
    listing = _chunk("Kafka, Postgres, Terraform", chunking.SECTION_SKILLS)
    ranked = retrieval.rerank("kafka partition rebalance", [listing, evidence], top_k=2)
    assert ranked[0] is evidence


def test_a_scorer_that_returns_nothing_degrades_to_fusion_order() -> None:
    """Reranking may only reorder within what fusion already thought plausible."""
    high = _chunk("first", score=0.9)
    low = _chunk("second", score=0.1)
    ranked = retrieval.rerank("query", [low, high], top_k=2, scorer=lambda q, c: 0.0)
    assert [chunk.score for chunk in ranked] == [0.9, 0.1]


def test_reranking_returns_at_most_top_k() -> None:
    chunks = [_chunk(f"content {index}") for index in range(10)]
    assert len(retrieval.rerank("content", chunks, top_k=3)) == 3


# ── assembly ─────────────────────────────────────────────────────────────────


def test_chunks_are_dropped_whole_and_the_drop_is_recorded() -> None:
    """Cutting the assembled string would hand a model half a sentence, and a
    model handed half a sentence completes it from its own priors."""
    # Distinct content per chunk: identical chunks would be collapsed by the
    # deduplication pass and never reach the budget at all.
    chunks = [
        _chunk(f"Region {index} ran the ingestion platform. " * 30) for index in range(6)
    ]
    assembled = context.assemble(chunks, query="ingestion", max_tokens=200)
    assert assembled.deduplicated == 0
    assert assembled.dropped > 0
    assert assembled.tokens <= 200
    assert len(assembled.chunks) < len(chunks)


def test_near_duplicates_are_collapsed_so_overlap_is_not_read_as_corroboration() -> None:
    shared = "Ran the consumer group rebalance during the cluster migration."
    chunks = [_chunk(shared), _chunk(shared + " "), _chunk("Built the warehouse on Iceberg.")]
    assembled = context.assemble(chunks, query="rebalance", max_tokens=2000)
    assert assembled.deduplicated == 1
    assert len(assembled.chunks) == 2


def test_every_piece_is_labelled_with_where_it_came_from() -> None:
    """An agent asked to ground a claim needs to be able to say WHICH evidence."""
    assembled = context.assemble([_chunk("Ran the rebalance.")], query="rebalance")
    assert "[resume:experience]" in assembled.text


def test_compression_is_extractive_and_invents_nothing() -> None:
    source = (
        "The team ran Kafka in three regions. Vacuum stopped keeping up on the "
        "warehouse. The rebalance took four hours. We repartitioned on tenant."
    )
    compressed = context.compress(source, "rebalance", max_tokens=12)
    assert compressed
    for sentence in compressed.split(". "):
        assert sentence.strip(". ") in source


def test_compression_keeps_the_sentences_that_answer_the_query() -> None:
    source = (
        "Unrelated background about the office move and the new laptops. "
        "We repartitioned the Kafka topics on tenant to cut replay time. "
        "More unrelated background about the quarterly planning process."
    )
    compressed = context.compress(source, "kafka repartition replay", max_tokens=20)
    assert "repartitioned the Kafka topics" in compressed


def test_compression_preserves_original_order() -> None:
    """A paragraph reordered by relevance reads as a non-sequitur, and its
    internal 'this' and 'that' stop referring to anything."""
    source = "Alpha kafka one. Beta unrelated two. Gamma kafka three."
    compressed = context.compress(source, "kafka", max_tokens=8)
    if "Alpha" in compressed and "Gamma" in compressed:
        assert compressed.index("Alpha") < compressed.index("Gamma")


def test_an_empty_retrieval_assembles_to_an_empty_context_not_a_crash() -> None:
    assembled = context.assemble([], query="anything")
    assert assembled.is_empty
    assert assembled.tokens == 0


def test_the_token_estimate_is_the_same_one_the_loop_uses() -> None:
    """Exactness is not the point; using one estimate everywhere is, because
    that is what makes two budgets comparable."""
    assert context.CHARS_PER_TOKEN == 4
    assert context.estimate_tokens("a" * 400) == 100
    assert context.estimate_tokens("") == 0


def test_the_keyword_retriever_ors_its_terms() -> None:
    """Found on the live index, not in a unit test.

    "kafka partition rebalance migration" matched NOTHING in a resume containing
    Kafka, partition and migration, because `plainto_tsquery` ANDs every term
    and the document lacked the word "rebalance". The lexical half exists to
    catch the exact token a JD demands, and requiring every word an agent
    happened to phrase its query with means it almost never fires -- silently,
    because fusion still returns the semantic hits.
    """
    tsquery = retrieval._tsquery("kafka partition rebalance migration")
    assert " | " in tsquery
    assert "&" not in tsquery
    assert "kafka" in tsquery


def test_the_tsquery_is_sanitised_before_it_reaches_postgres() -> None:
    """`to_tsquery` parses operators from its input, so an unsanitised query
    containing & or ! is a syntax error at best."""
    tsquery = retrieval._tsquery("kafka & postgres ! (redis)")
    for operator in ("&", "!", "(", ")"):
        assert operator not in tsquery


def test_an_empty_query_produces_no_tsquery_rather_than_a_broken_one() -> None:
    assert retrieval._tsquery("") == ""
    assert retrieval._tsquery("!!!") == ""

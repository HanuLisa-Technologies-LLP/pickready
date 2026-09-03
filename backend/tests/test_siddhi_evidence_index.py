"""The citable set Siddhi writes from, and the gap case it exists to serve.

`test_siddhi_citations` proves the CHOKEPOINT: `Section.render` raises on an
uncited statement and there is no bypass. This file covers the thing the
chokepoint reads from -- the index that decides WHICH ref a claim about an item
is allowed to rest on.

The arm that matters most is the one with no answers in it. A gap statement
("there is no evidence of X") feels uncitable, and the citation is the evidence
that was SEARCHED; without it a gap in the assessment is reported as a gap in
the candidate. So an item nobody answered anything about must still index a
`searched` node, and `grounding` must fall back to it rather than returning
nothing and pushing the generator into writing an uncited sentence.

Pure data structures. No database, no network, no model.
"""
from __future__ import annotations

from app.services.siddhi.evidence import (
    KIND_ANSWER,
    KIND_QUESTION,
    KIND_SEARCHED,
    EvidenceIndex,
)


def _exchange(question: str, answer: str) -> dict:
    # `build` takes the excerpt from the answer text itself, truncated. There
    # is no separate excerpt key, and inventing one here would test a contract
    # the module does not have.
    return {"question": question, "answer": answer}


# ── The gap case ─────────────────────────────────────────────────────────────


def test_an_item_nobody_answered_still_has_something_to_cite() -> None:
    """The reason `items` is passed separately from the exchanges. Without a
    `searched` node the generator has no ref for the gap statement and either
    omits the gap or writes it uncited."""
    index = EvidenceIndex.build(items=["Kafka"], exchanges={})
    searched = index.searched("Kafka")
    assert searched, "an unanswered item must still be citable"
    assert index.grounding("Kafka") == searched


def test_an_answered_item_grounds_on_its_answers_not_on_the_search_record() -> None:
    """A claim resting on the search record alone is a weaker claim, and the
    ref says so by its kind; mixing the two would hide that."""
    index = EvidenceIndex.build(
        items=["Kafka"],
        exchanges={"Kafka": [_exchange("How have you used Kafka?", "Rebalanced partitions.")]},
    )
    grounding = index.grounding("Kafka")
    answers = index.refs_for("Kafka", kind=KIND_ANSWER)
    assert grounding == answers
    assert set(grounding).isdisjoint(index.searched("Kafka"))


# ── Keying and lookup ────────────────────────────────────────────────────────


def test_every_ref_in_the_index_is_unique() -> None:
    """`citations.Report` is constructed from this set, so a duplicate ref
    would let one node's citation vouch for another's claim."""
    index = EvidenceIndex.build(
        items=["Kafka", "Postgres"],
        exchanges={
            "Kafka": [_exchange("q1", "a1"), _exchange("q2", "a2")],
            "Postgres": [_exchange("q3", "a3")],
        },
    )
    refs = [node.ref for node in index.nodes]
    assert len(refs) == len(set(refs))
    assert index.refs == frozenset(refs)


def test_two_names_that_slugify_alike_do_not_merge_their_evidence() -> None:
    """A slug collision would silently attribute one criterion's answers to
    another, and the report would cite evidence the candidate gave about
    something else."""
    index = EvidenceIndex.build(
        items=["Kafka: streaming", "Kafka streaming"],
        exchanges={
            "Kafka: streaming": [_exchange("q1", "first answer")],
            "Kafka streaming": [_exchange("q2", "second answer")],
        },
    )
    first = index.refs_for("Kafka: streaming", kind=KIND_ANSWER)
    second = index.refs_for("Kafka streaming", kind=KIND_ANSWER)
    assert first and second
    assert set(first).isdisjoint(second)


def test_a_repeated_item_is_indexed_once() -> None:
    index = EvidenceIndex.build(items=["Kafka", "Kafka"], exchanges={})
    assert len(index.searched("Kafka")) == 1


def test_an_unknown_item_yields_nothing_rather_than_raising() -> None:
    """The generator asks about items it is rendering; an unknown one is a
    programming error upstream, and returning empty lets the chokepoint report
    it as an uncited statement rather than a crash mid-report."""
    index = EvidenceIndex.build(items=["Kafka"], exchanges={})
    assert index.for_item("Cassandra") == ()
    assert index.refs_for("Cassandra") == ()
    assert index.grounding("Cassandra") == ()


# ── Excerpts and exchanges ───────────────────────────────────────────────────


def test_the_excerpt_comes_from_an_answer_that_has_one() -> None:
    index = EvidenceIndex.build(
        items=["Kafka"],
        exchanges={
            "Kafka": [
                _exchange("q1", ""),
                _exchange("q2", "rebalanced the partitions"),
            ]
        },
    )
    # The first exchange records no answer at all, so the excerpt has to come
    # from the second rather than reading as empty.
    assert index.excerpt("Kafka") == "rebalanced the partitions"


def test_no_excerpt_anywhere_reads_as_empty_rather_than_none() -> None:
    index = EvidenceIndex.build(items=["Kafka"], exchanges={})
    assert index.excerpt("Kafka") == ""


def test_exchanges_pair_each_question_with_its_own_answer_in_order() -> None:
    index = EvidenceIndex.build(
        items=["Kafka"],
        exchanges={
            "Kafka": [
                _exchange("first question", "first answer"),
                _exchange("second question", "second answer"),
            ]
        },
    )
    pairs = index.exchanges("Kafka")
    assert len(pairs) == 2
    question_refs = index.refs_for("Kafka", kind=KIND_QUESTION)
    answer_refs = index.refs_for("Kafka", kind=KIND_ANSWER)
    assert [p[0] for p in pairs] == list(question_refs)
    assert [p[1] for p in pairs] == list(answer_refs)
    assert [p[2] for p in pairs] == ["first answer", "second answer"]


def test_an_item_with_no_exchanges_pairs_nothing() -> None:
    index = EvidenceIndex.build(items=["Kafka"], exchanges={})
    assert index.exchanges("Kafka") == ()


def test_the_searched_node_is_not_mistaken_for_an_answer() -> None:
    """Kind is what tells a strong claim from a gap statement, so the search
    record must never appear under the answer kind."""
    index = EvidenceIndex.build(items=["Kafka"], exchanges={})
    assert index.refs_for("Kafka", kind=KIND_ANSWER) == ()
    assert index.refs_for("Kafka", kind=KIND_SEARCHED) != ()
    assert set(index.refs_for("Kafka")) == set(index.refs_for("Kafka", kind=KIND_SEARCHED))

"""THE OBSERVABLE-EVIDENCE QUALITY BAR. This file is the specification.

Runbook §16.3 prints two pairs and calls the conversion "the single
highest-leverage part of the whole intake, because unobservable criteria are
exactly where bias enters". spec-doc6 §4.2 says to build the test table from
those pairs and assert every rejected example is rejected and every accepted
example is accepted, and that the test IS the specification.

So that is what this file does, and it does it against the Runbook document
rather than against a copy: `Readypick Hiring Philosophy.md` is in the
repository and spec-doc6 §0.2 puts it above every other document. Everything
else here is a parity check that the two derived copies still say what it says.

THREE SOURCES, ONE BAR
----------------------
    the Runbook          `dna_compilation.runbook_source_example_pairs()`
    the extraction       `runbook_data/company_dna_instrument.yaml`
    the instrument       `company_dna.SECTIONS[...].examples`

The instrument's copy is what the intake screen SHOWS a client, so a drift
between it and the Runbook would put one wording in the document and a
different one in front of the person being asked. The extraction is what the
rest of the codebase reads. All three are compared, in both directions.
"""
from __future__ import annotations

import pytest

from app.services.hiring import company_dna
from app.services.hiring import dna_compilation


def _normalise(text: str) -> str:
    """Trailing punctuation and surrounding quotes are not the quality bar.

    The Runbook prints `Rejected: "Ownership mindset."` and the instrument
    carries `Ownership mindset` without the full stop. Comparing those as
    different strings would fail a parity test over a typographic detail while
    a genuinely reworded example passed, which is the wrong sensitivity in both
    directions.
    """
    return " ".join(text.replace("…", "...").split()).strip(' "\'.').lower()


RUNBOOK_PAIRS = dna_compilation.runbook_source_example_pairs()


# ── The bar itself ───────────────────────────────────────────────────────────


def test_the_runbook_prints_the_pairs_this_file_is_built_from() -> None:
    """A table built from an empty read passes forever.

    Not hypothetical in this repository: `test_platform_audit` once resolved
    its scan root to a path that does not exist in the container and reported
    six product rules green while reading zero files.
    """
    assert len(RUNBOOK_PAIRS) >= 2, (
        "the Runbook's observable-evidence section yielded "
        f"{len(RUNBOOK_PAIRS)} example pairs; the quality bar has no content"
    )
    rejected = {_normalise(pair.rejected) for pair in RUNBOOK_PAIRS}
    assert "ownership mindset" in rejected
    assert "team player" in rejected


@pytest.mark.parametrize(
    "rejected", [pair.rejected for pair in RUNBOOK_PAIRS], ids=_normalise
)
def test_every_runbook_rejected_example_is_rejected(rejected: str) -> None:
    """An adjective is refused.

    "Ownership mindset" is not a criterion; it is a compliment with no event in
    it. A candidate cannot evidence it, an evaluator cannot cite anything for
    it, and a report grading it cannot say why. Accepting one here is how it
    becomes a competency nobody can defend.
    """
    assert not company_dna.is_observable(rejected), (
        f"{rejected!r} is one of the Runbook's REJECTED examples and the "
        "instrument accepted it"
    )


@pytest.mark.parametrize(
    "accepted", [pair.accepted for pair in RUNBOOK_PAIRS], ids=lambda s: _normalise(s)[:40]
)
def test_every_runbook_accepted_example_is_accepted(accepted: str) -> None:
    """The rewrite is accepted.

    This half is the one that matters more in practice. A detector that
    refused everything would pass the rejection half of this table perfectly
    and make the instrument unusable, and the client would learn that no answer
    is ever good enough rather than what a good answer looks like.
    """
    assert company_dna.is_observable(accepted), (
        f"{accepted!r} is one of the Runbook's ACCEPTED examples and the "
        "instrument refused it"
    )


def test_a_rejection_shows_the_difference_rather_than_only_refusing() -> None:
    """The refusal has to teach, or the client rephrases the same adjective.

    §16.3 makes the recruiter responsible for enforcing the conversion. Bodha
    is doing that job here, so its refusal names the phrase it caught and shows
    an accepted rewrite.
    """
    for pair in RUNBOOK_PAIRS:
        message = company_dna.rejection_message(pair.rejected)
        assert message.strip(), "an empty refusal teaches nothing"
        assert "Has " in message or "has " in message, (
            "the refusal shows no accepted example, so the client has nothing "
            f"to convert towards: {message!r}"
        )
        # A character class that MATCHES a dash is data, not prose: built from
        # chr(8212) so a repo-wide sweep cannot rewrite the check itself.
        assert chr(8212) not in message, "em dash in a client-facing string"


# ── Parity: the two derived copies say what the Runbook says ─────────────────


def test_the_instrument_carries_the_runbook_pairs_and_no_others() -> None:
    """What a client is SHOWN is the Runbook's wording.

    Both directions. A missing pair means the screen teaches less than the
    Runbook does; an extra one means somebody invented an example and it now
    reads as the Runbook's.
    """
    runbook = {
        (_normalise(p.rejected), _normalise(p.accepted)) for p in RUNBOOK_PAIRS
    }
    shown = {
        (_normalise(p.rejected), _normalise(p.accepted))
        for p in dna_compilation.instrument_example_pairs()
    }
    assert shown, "the instrument shows no example pairs at all"
    assert shown <= runbook, f"the instrument shows pairs the Runbook does not: {shown - runbook}"
    assert runbook <= shown, f"the Runbook prints pairs the instrument omits: {runbook - shown}"


def test_the_extracted_runbook_data_agrees_with_the_runbook() -> None:
    """`runbook_data/company_dna_instrument.yaml` is an extraction, not a source.

    If the extraction is absent this fails naming the file rather than
    skipping. A skip here would mean the quality bar quietly stopped being
    checked against the copy the rest of the codebase reads.
    """
    try:
        extracted = dna_compilation.runbook_example_pairs()
    except dna_compilation.RunbookDataUnavailable as exc:
        pytest.fail(str(exc))
    runbook = {
        (_normalise(p.rejected), _normalise(p.accepted)) for p in RUNBOOK_PAIRS
    }
    found = {(_normalise(p.rejected), _normalise(p.accepted)) for p in extracted}
    assert runbook <= found, (
        f"{dna_compilation.RUNBOOK_DATA_FILE} is missing pairs the Runbook "
        f"prints: {runbook - found}"
    )


def test_the_loader_names_the_missing_file_rather_than_defaulting(monkeypatch) -> None:
    """The absent-extraction path fails loudly and says which file.

    A fallback to a hardcoded pair would be a quality bar that had silently
    stopped being the Runbook's, which is the failure this whole file exists to
    prevent. Exercised with a stub because the file is present today, and the
    behaviour has to be pinned for the day somebody deletes it.
    """
    import sys

    import app.services.hiring as hiring_pkg

    # BOTH have to go. `from pkg import sub` reads the parent package's
    # attribute before it consults sys.modules, so deleting only the module
    # entry leaves the already-imported submodule reachable and the test passes
    # while proving nothing.
    monkeypatch.delattr(hiring_pkg, "runbook_data", raising=False)
    monkeypatch.setitem(sys.modules, "app.services.hiring.runbook_data", None)
    with pytest.raises(dna_compilation.RunbookDataUnavailable) as caught:
        dna_compilation.runbook_example_pairs()
    assert dna_compilation.RUNBOOK_DATA_FILE in str(caught.value)


def test_a_loader_that_lost_its_entry_point_is_also_named(monkeypatch) -> None:
    """The second way the extraction can be unusable: present, but changed.

    An extraction whose loader was renamed would otherwise surface as an
    AttributeError somewhere downstream, which names the symbol and not the
    file a person has to go and fix.
    """
    import app.services.hiring.runbook_data as runbook_data

    monkeypatch.delattr(runbook_data, "company_dna_instrument", raising=False)
    with pytest.raises(dna_compilation.RunbookDataUnavailable) as caught:
        dna_compilation.runbook_example_pairs()
    assert dna_compilation.RUNBOOK_DATA_FILE in str(caught.value)


# ── The bar catches the shapes the Runbook is aiming at ──────────────────────

#: Written from §16.3's rule rather than from its two printed pairs, so the
#: detector is exercised past the exact strings it could otherwise special-case.
#: Every rejected entry is the shape §16.3 refuses: a description of a person
#: rather than an account of an event. Every accepted entry follows Appendix
#: A3's format, "Has [done X] and can [describe or demonstrate Y]".
_EXTRA_REJECTED = (
    "Ownership mindset",
    "Team player",
    "Strong communicator",
    "Detail-oriented",
    "Culture fit",
    "A self-starter with a proactive attitude and real hunger",
)

_EXTRA_ACCEPTED = (
    "Has taken a project from an unclear brief to a shipped outcome and can "
    "describe the decisions they made when nobody told them what to do",
    "Has moved a blocking function to a decision without holding authority "
    "over it and can describe how they secured the commitment",
    "Has taken an unpredictable team to a predictable cadence and can describe "
    "the mechanism they introduced",
)


@pytest.mark.parametrize("phrase", _EXTRA_REJECTED)
def test_the_shape_the_runbook_refuses_is_refused(phrase: str) -> None:
    assert not company_dna.is_observable(phrase)


@pytest.mark.parametrize("phrase", _EXTRA_ACCEPTED, ids=lambda s: s[:30])
def test_the_shape_appendix_a3_asks_for_is_accepted(phrase: str) -> None:
    assert company_dna.is_observable(phrase)


# ── The instrument the screens serve is the Runbook's instrument ─────────────
#
# The API renders questions out of `company_dna.SECTIONS`, which is the module
# that ADMINISTERS the instrument. That is deliberately one source and not two:
# a screen that took its question from one place and its example from another
# would eventually show a §16.3 example beside a question §16.3 no longer asks.
#
# What keeps that source honest is this block. `runbook_data` is the extraction
# the parity suite checks against the Runbook itself, so comparing the
# instrument to the extraction closes the chain: Runbook -> extraction ->
# instrument -> screen, with a test at every link.


def _extract():
    from app.services.hiring import runbook_data

    return runbook_data.company_dna_instrument()


def test_the_instrument_has_the_twelve_sections_the_runbook_names() -> None:
    extracted = _extract()
    assert extracted["administration"]["section_count"] == 12
    runbook_titles = [section["title"] for section in extracted["sections"]]
    instrument_titles = [section.title for section in company_dna.SECTIONS]
    assert instrument_titles == runbook_titles, (
        "the sections a client is asked are not the sections the Runbook names, "
        "in this order"
    )


def test_section_two_is_six_forced_scales_in_the_runbooks_order() -> None:
    """§16.2 and Appendix A2 items 7 to 12.

    The order matters as much as the count: the questions are asked in
    instrument order, and a client reading them in a different order from the
    field form is being administered a different instrument.
    """
    extracted = _extract()
    section = extracted["sections"][1]
    assert section["response_type"] == "forced_scale"
    questions = [q for q in company_dna.SECTIONS[1].questions]
    assert len(questions) == len(section["questions"]) == 6
    for question, runbook in zip(questions, section["questions"]):
        assert question.kind == company_dna.SCALE_QUESTION, question.key
        assert question.poles is not None, question.key
        assert company_dna.SCALE_MIN == runbook["scale_minimum"]
        assert company_dna.SCALE_MAX == runbook["scale_maximum"]


def test_the_observable_evidence_section_asks_for_the_stated_range() -> None:
    """RUNBOOK-AMBIGUITY (§16.3): the section says "five to eight behaviours"
    and Appendix A3 prints five blank slots. Resolved as a repeating field
    accepting five to eight (RUNBOOK_OPEN_QUESTIONS.md Q11), which is what the
    instrument carries and what the API enforces."""
    section = next(
        s for s in company_dna.SECTIONS if s.key == "observable_evidence"
    )
    assert (section.min_items, section.max_items) == (5, 8)
    assert any(
        q.kind == company_dna.EVIDENCE_LIST_QUESTION for q in section.questions
    ), "the range is only meaningful on a repeating field"

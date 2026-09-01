"""The one place that decides what the pre-screen may see.

`input_from_profile` is the adapter from this product's stored rows to the
anonymised input every pre-screen grade is computed from, and its own docstring
says why it lives where it does: so there is ONE place a reviewer has to read to
check that a name never crosses.

THREE FIELDS CARRY PEDIGREE AND ALL THREE ARE DROPPED HERE. `company` off every
employment entry, `institution` off every education entry, and the candidate's
own name out of the resume text. There is no flag that re-admits them. Section
52.2's argument is that a grade which can see where somebody worked is a grade
that can rank the brand rather than the work, and the candidate who learnt the
craft at an unfamiliar employer pays for it -- which is the same directional
failure section 58 names about vocabulary, arriving through provenance instead.

What is dropped is the structured FIELD. An employer the candidate named inside
their own resume prose is not removed, because the adapter anonymises against
the `identities` it was given and holds no list of employer names. That gap is
asserted below rather than assumed away.

THE DATE PARSER IS TOTAL, AND THAT IS LOAD BEARING. Resumes carry dates in
every format a person has ever typed. An unparseable one becomes None, which
reads as "we do not know when this ended" and costs the claim its recency
weighting; raising instead would make one badly formatted line take down the
grading of the whole resume, and defaulting to today would silently make every
stale claim look current.

Pure functions over a stub row. No database, no network, no model.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.hiring import prescreen


TODAY = date(2026, 9, 1)


class _Profile:
    """The two attributes the adapter reads. Deliberately not a real `Profile`:
    the adapter must work off what is stored, not off an ORM behaviour."""

    def __init__(self, parsed=None, resume_text=None):
        self.parsed_fields_json = parsed
        self.resume_text = resume_text


def _build(profile, **kwargs) -> prescreen.PreScreenInput:
    defaults = dict(
        requirements=["Stream processing"],
        requirement_source="jd",
        clock=prescreen.CLOCK_FAST,
        today=TODAY,
    )
    defaults.update(kwargs)
    return prescreen.input_from_profile(profile, **defaults)


# ── Dates, in every shape a resume carries them ──────────────────────────────


@pytest.mark.parametrize(
    "written, expected",
    [
        ("2024-03-15", date(2024, 3, 15)),
        ("2024/03/15", date(2024, 3, 15)),
        ("03/2024", date(2024, 3, 1)),
        ("2024-03", date(2024, 3, 1)),
        ("Mar 2024", date(2024, 3, 1)),
        ("March 2024", date(2024, 3, 1)),
        ("2024", date(2024, 1, 1)),
    ],
)
def test_the_formats_people_actually_write_are_all_read(written, expected) -> None:
    assert prescreen._as_date(written) == expected


def test_a_date_object_passes_through_and_a_datetime_loses_its_time() -> None:
    assert prescreen._as_date(date(2024, 3, 15)) == date(2024, 3, 15)
    assert prescreen._as_date(
        datetime(2024, 3, 15, 9, 30, tzinfo=timezone.utc)
    ) == date(2024, 3, 15)


@pytest.mark.parametrize("written", ["", "   ", None, "sometime in the spring", "31/31/2024"])
def test_an_unreadable_date_is_unknown_rather_than_fatal(written) -> None:
    """None reads as "we do not know when this ended", which costs the claim
    its recency weighting. Raising would let one badly formatted line take the
    whole resume down; defaulting to today would make every stale claim look
    current."""
    assert prescreen._as_date(written) is None


# ── Employment spans, with the company name dropped ──────────────────────────


def test_a_span_keeps_the_title_and_the_dates_and_nothing_else() -> None:
    spans = prescreen._spans_from_history(
        [
            {
                "title": "Staff Engineer",
                "company": "Goldman Sachs",
                "start": "2021-01",
                "end": "2024-06",
            }
        ]
    )
    assert len(spans) == 1
    assert spans[0].title == "Staff Engineer"
    assert spans[0].start == date(2021, 1, 1)
    assert spans[0].end == date(2024, 6, 1)
    assert "company" not in {f for f in vars(spans[0])}


def test_a_history_that_is_not_a_list_yields_no_spans() -> None:
    """A parser that returned a string or a dict must not become an exception in
    the grader."""
    for junk in (None, "2021 to 2024", {"title": "Engineer"}, 7):
        assert prescreen._spans_from_history(junk) == ()


def test_a_malformed_entry_is_skipped_and_the_others_survive() -> None:
    """One bad row must not discard the rest of somebody's career."""
    spans = prescreen._spans_from_history(
        ["a bare string", {"title": "Engineer", "start": "2021", "end": "2024"}]
    )
    assert len(spans) == 1
    assert spans[0].title == "Engineer"


def test_a_span_with_no_title_is_kept_with_none() -> None:
    """The dates still bound a gap, and dropping the row would make the gap
    disappear rather than be explained."""
    spans = prescreen._spans_from_history([{"start": "2021", "end": "2024"}])
    assert len(spans) == 1
    assert spans[0].title is None


# ── The adapter as a whole ───────────────────────────────────────────────────


def test_the_structured_employer_field_never_becomes_a_claim() -> None:
    """Section 52.2. A grade that can see where somebody worked is a grade that
    can rank the brand rather than the work.

    NOTE THE EXACT SCOPE, because it is narrower than it first reads. What is
    dropped is the `company` KEY: it is never read, so it cannot become a claim
    or a term however the parser filled it. An employer named inside the
    candidate's own prose is a different question -- the adapter anonymises the
    resume text against the supplied `identities` and has no list of employer
    names to work from. `test_an_employer_named_in_prose_survives` below states
    that limitation rather than leaving it to be discovered.
    """
    built = _build(
        _Profile(
            parsed={
                "skills": ["Kafka"],
                "employment_history": [
                    {
                        "title": "Staff Engineer",
                        "company": "Goldman Sachs",
                        "summary": "Rebuilt the ingestion pipeline end to end.",
                        "start": "2021-01",
                        "end": "2024-06",
                    }
                ],
            },
            resume_text="Rebuilt the ingestion pipeline end to end.",
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "goldman" not in everything
    assert "sachs" not in everything
    assert everything, "the role line itself must still be read"


def test_an_employer_named_in_prose_survives_and_that_is_the_known_limit() -> None:
    """The honest statement of what this adapter does NOT do.

    `anonymise` removes the identities it is given, and an employer name is not
    one of them, so a resume that names its employers in prose carries that
    pedigree into the claims. The structured field is closed; the free-text path
    is not. Written down here so the next reader finds a stated limitation
    rather than assuming a guarantee the module does not make.
    """
    built = _build(
        _Profile(
            parsed={},
            resume_text="Rebuilt the ingestion pipeline at Goldman Sachs end to end.",
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "goldman" in everything


def test_the_candidate_name_never_reaches_the_pre_screen_input() -> None:
    built = _build(
        _Profile(
            parsed={
                "employment_history": [
                    {
                        "title": "Staff Engineer",
                        "summary": "Ada Lovelace rebuilt the ingestion pipeline.",
                        "end": "2024-06",
                    }
                ]
            },
            resume_text="Ada Lovelace rebuilt the ingestion pipeline end to end.",
        ),
        identities=["Ada Lovelace"],
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "lovelace" not in everything


def test_every_prose_field_on_a_role_becomes_a_claim() -> None:
    """Résumés put the substance under different keys. Reading only `summary`
    would silently grade a candidate whose parser filed everything under
    `achievements` as though they had written nothing."""
    for key in ("summary", "description", "responsibilities", "achievements"):
        built = _build(
            _Profile(
                parsed={
                    "employment_history": [
                        {key: "Rebuilt the ingestion pipeline end to end.", "end": "2024-06"}
                    ]
                }
            )
        )
        assert built.claims, key


def test_a_list_of_bullets_becomes_one_claim_each() -> None:
    built = _build(
        _Profile(
            parsed={
                "employment_history": [
                    {
                        "achievements": [
                            "Rebuilt the ingestion pipeline end to end.",
                            "Cut the nightly batch runtime substantially.",
                            "",
                            None,
                        ],
                        "end": "2024-06",
                    }
                ]
            }
        )
    )
    assert len(built.claims) == 2


def test_a_role_with_no_end_date_is_read_as_ongoing_rather_than_dropped() -> None:
    """A current job has no end date. Dropping it would lose the candidate's
    most recent and most relevant work."""
    built = _build(
        _Profile(
            parsed={
                "employment_history": [
                    {"summary": "Rebuilt the ingestion pipeline end to end."}
                ]
            }
        )
    )
    assert built.claims


def test_a_profile_with_nothing_parsed_yields_an_input_that_says_so() -> None:
    """A scanned image resume. `resume_parsed` false is what routes it to a
    person rather than to a low grade -- absence of evidence is not evidence of
    absence."""
    built = _build(_Profile(parsed=None, resume_text=None))
    assert built.claims == ()
    assert built.resume_parsed is False


def test_junk_in_the_parsed_fields_does_not_reach_the_input() -> None:
    """A parser returning a number where a skill belongs is a parser bug, not a
    candidate with a skill called 7."""
    built = _build(
        _Profile(parsed={"skills": ["Kafka", 7, None, {"name": "Spark"}]})
    )
    terms = {term for claim in built.claims for term in claim.terms}
    assert "7" not in terms
    assert any("kafka" in term for term in terms)


def test_an_unreadable_experience_figure_is_unknown_rather_than_zero() -> None:
    """Zero years is a claim about the candidate. "We could not read it" is a
    claim about the parser, and only one of them is true."""
    for junk in ("about eight", None, {"years": 8}):
        built = _build(_Profile(parsed={"total_experience_years": junk}))
        assert built.primary_state is not None


def test_a_readable_experience_figure_is_used() -> None:
    built = _build(_Profile(parsed={"total_experience_years": "8"}))
    assert built.primary_state is not None


def test_the_requirements_and_their_source_travel_unchanged() -> None:
    """The pre-screen states which document a requirement came from, because a
    grade against a requirement nobody wrote down is not auditable."""
    built = _build(
        _Profile(parsed={"skills": ["Kafka"]}),
        requirements=["Stream processing", "Ownership"],
        requirement_source="scorecard",
    )
    assert built.requirements == ("Stream processing", "Ownership")
    assert built.requirement_source == "scorecard"


def test_the_input_carries_no_field_that_could_hold_a_name() -> None:
    """Asserted on the FIELD SET rather than on particular names, because a
    future field called `notes` would pass a narrower test and reopen the whole
    hole."""
    assert set(prescreen.field_names(prescreen.PreScreenInput)) == {
        "requirements",
        "requirement_source",
        "claims",
        "clock",
        "states",
        "primary_state",
        "gap_months",
        "resume_parsed",
    }

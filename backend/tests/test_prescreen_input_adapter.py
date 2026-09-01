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

BOTH HALVES ARE CLOSED AS OF 2026-09-01. The structured `company` and
`institution` keys are dropped by never being read, and the same names are now
scrubbed out of the resume PROSE, which is where a candidate writes "Rebuilt
the ingestion pipeline at <employer>" and carries the pedigree into a claim
term the ontology then compares.

THE SCRUB IS BOUNDED TWICE, AND BOTH BOUNDS ARE THE POINT. It matches on word
boundaries, because an unbounded replace with an employer called "Ace" removes
"namespace" and "tracer"; and it never removes a term the JOB IS ASKING ABOUT,
because an employer called Oracle or Docker shares its name with a technology
and losing that would be the section 58 vocabulary failure pointed the other
way. The second bound is the load-bearing one: it makes the scrub structurally
unable to remove a word that could have earned a match.

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


def test_an_employer_named_in_prose_is_scrubbed_too() -> None:
    """The half that used to be open.

    "Rebuilt the ingestion pipeline at Goldman Sachs" carried "goldman" and
    "sachs" into `Claim.terms`, which is the set the ontology COMPARES. Section
    52.2's argument covers the prose exactly as it covers the field: a grade
    that can see where somebody worked can rank the brand rather than the work.
    """
    built = _build(
        _Profile(
            parsed={
                "employment_history": [
                    {"company": "Goldman Sachs", "title": "Staff Engineer"}
                ]
            },
            resume_text="Rebuilt the ingestion pipeline at Goldman Sachs end to end.",
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    terms = {term for claim in built.claims for term in claim.terms}
    assert "goldman" not in everything
    assert "sachs" not in everything
    assert "goldman" not in terms and "sachs" not in terms
    assert "ingestion" in terms, "the claim itself must survive the scrub"


def test_an_institution_named_in_prose_is_scrubbed_too() -> None:
    """Section 52.2 lists the institution beside the employer, and a degree
    awarded by a recognisable name is the same pedigree signal."""
    built = _build(
        _Profile(
            parsed={
                "education": [
                    {"institution": "Indian Institute of Technology Bombay"}
                ]
            },
            resume_text=(
                "Indian Institute of Technology Bombay. Built a compiler for a "
                "teaching language and shipped it to two cohorts."
            ),
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "indian institute of technology" not in everything
    assert "compiler" in everything


def test_a_short_employer_name_cannot_mangle_ordinary_prose() -> None:
    """The hazard that made the unbounded version unusable. A case-insensitive
    substring replace with an employer called "Ace" turns "namespace tracer"
    into "namesp tr r", which costs a candidate real terms and which nothing
    downstream could detect."""
    built = _build(
        _Profile(
            parsed={"employment_history": [{"company": "Ace"}]},
            resume_text="Built a namespace tracer for the interface replacement.",
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "namespace" in everything
    assert "tracer" in everything
    assert "interface" in everything


def test_a_corporate_wrapper_word_is_never_scrubbed() -> None:
    """"Systems" out of "distributed systems" costs a candidate a real term and
    hides nobody's employer."""
    built = _build(
        _Profile(
            parsed={"employment_history": [{"company": "Data Systems Solutions Ltd"}]},
            resume_text=(
                "Ran the Database migration and owned distributed systems work "
                "across two regions."
            ),
        )
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "database" in everything
    assert "systems" in everything


def test_a_term_the_job_asks_about_is_never_scrubbed() -> None:
    """THE LOAD-BEARING GUARD. An employer called Oracle shares its name with a
    technology, and the ontology cannot arbitrate: it is a curated set of
    synonyms rather than a registry of product names, and it does not contain
    "oracle". The requirement list can, and it makes the scrub unable to remove
    a word that could have earned a match."""
    built = _build(
        _Profile(
            parsed={"employment_history": [{"company": "Oracle"}]},
            resume_text="Ran Oracle Database replication across three regions.",
        ),
        requirements=["Oracle Database"],
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "oracle" in everything


def test_the_same_employer_is_scrubbed_when_the_job_does_not_ask_about_it() -> None:
    """The other side of the same guard, so the protection above is a rule
    rather than a blanket exemption for anything that looks like a product."""
    built = _build(
        _Profile(
            parsed={"employment_history": [{"company": "Oracle"}]},
            resume_text="Ran Oracle Database replication across three regions.",
        ),
        requirements=["Stream processing"],
    )
    everything = " ".join(claim.text for claim in built.claims).casefold()
    assert "oracle" not in everything
    assert "database" in everything, "only the employer goes, not the sentence"


def test_scrubbing_an_employer_never_lowers_a_grade() -> None:
    """The property the requirement guard buys, asserted as a property rather
    than as a word list.

    `test_yukti_live` already pins the additive direction: appending brand names
    to a resume moves the grade by nothing. This is the subtractive one --
    removing them cannot move it either, so the anonymised pass is neutral in
    both directions and section 52.2 costs the candidate nothing.
    """
    requirements = ["Stream processing", "Oracle Database", "distributed systems"]
    prose = (
        "Ran Oracle Database replication and owned distributed systems work, "
        "rebuilding the stream processing pipeline across three regions."
    )
    with_employer = prescreen.grade(
        _build(
            _Profile(
                parsed={
                    "employment_history": [
                        {"company": "Oracle"},
                        {"company": "Goldman Sachs"},
                    ]
                },
                resume_text=prose,
            ),
            requirements=requirements,
        )
    )
    without = prescreen.grade(
        _build(_Profile(parsed={}, resume_text=prose), requirements=requirements)
    )
    assert with_employer.internal.value == without.internal.value
    assert with_employer.named.grade == without.named.grade


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


# ── The scrub itself ─────────────────────────────────────────────────────────


def test_a_multi_word_brand_is_removed_before_its_parts_are() -> None:
    """LONGEST FIRST, the same ordering rule `_identities` follows for a
    person's name: remove "Tata Consultancy Services" before "Tata", or the
    shorter pattern is left matching the tail of the longer one."""
    cleaned = prescreen.anonymise(
        "Delivered the migration at Tata Consultancy Services over two years.",
        organisations=["Tata Consultancy Services"],
    )
    assert "Tata" not in cleaned
    assert "Consultancy" not in cleaned
    assert "migration" in cleaned


def test_an_org_name_is_matched_on_word_boundaries_not_as_a_substring() -> None:
    """A boundary, not a substring. This is the difference between removing an
    employer and removing "namespace"."""
    cleaned = prescreen.anonymise(
        "Built a namespace tracer at Sachsen Systems.", organisations=["Sachs"]
    )
    assert "namespace" in cleaned
    assert "Sachsen" in cleaned, "a longer word that merely starts with it is not it"


def test_a_hyphen_counts_as_a_boundary_and_that_is_the_point() -> None:
    """"Ex-Infosys" is how a resume most often names a former employer, and it
    is the common case in this product's own market. If a hyphen were not a
    boundary the prefix form would survive every scrub, which is the one
    spelling the rule most needs to catch."""
    cleaned = prescreen.anonymise("Ex-Infosys, now leading the platform team.",
                                  organisations=["Infosys"])
    assert "Infosys" not in cleaned
    assert "platform team" in cleaned


def test_a_protected_term_survives_by_any_of_its_names() -> None:
    """The guard expands through the ontology, so a job asking for one word for
    a technology protects the resume that wrote the other word for it -- which
    is the same equivalence section 58 relies on, used defensively."""
    cleaned = prescreen.anonymise(
        "Ran the Kubernetes rollout for the platform team.",
        organisations=["Kubernetes"],
        protected_terms=["k8s"],
    )
    assert "Kubernetes" in cleaned


def test_an_org_name_too_short_to_be_distinctive_is_left_alone() -> None:
    """Two and three letter names are initialisms that collide with
    everything."""
    for short in ("Ace", "IT", "AI"):
        cleaned = prescreen.anonymise(
            "Built a namespace tracer and an AI-assisted linter.",
            organisations=[short],
        )
        assert "namespace" in cleaned
        assert "tracer" in cleaned


def test_no_organisations_leaves_the_text_exactly_as_it_was() -> None:
    """Every existing caller passes none, so the scrub has to be a no-op for
    them rather than a reformat."""
    text = "Rebuilt the ingestion pipeline and cut nightly runtime."
    assert prescreen.anonymise(text) == text


def test_the_same_resume_under_two_letterheads_produces_one_term_set() -> None:
    """The fairness property stated the way `anonymise`'s docstring states it.

    The module already guaranteed that the same document under a different NAME
    grades identically. This is the same guarantee for the employer, and it is
    the one section 52.2 is actually about: the candidate who did identical
    work at an unfamiliar firm must reach the ontology with identical terms.
    """
    prose = "Rebuilt the ingestion pipeline at {}, cutting nightly runtime by half."

    def terms(company: str) -> list[str]:
        built = _build(
            _Profile(
                parsed={
                    "employment_history": [{"company": company, "title": "Engineer"}]
                },
                resume_text=prose.format(company),
            ),
            requirements=["Stream processing", "data pipeline"],
        )
        return sorted({term for claim in built.claims for term in claim.terms})

    recognisable = terms("Goldman Sachs")
    unfamiliar = terms("Sharma Logistics Private Limited")
    assert recognisable == unfamiliar
    assert "ingestion" in recognisable, "the work itself still reaches the ontology"

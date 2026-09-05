"""The candidate's Updates feed (workflow sections 14 and 15).

The feed exists because email is the product's only other channel to a
candidate and email silently fails: a spam filter, a full inbox, or a typo in
an address a recruiter uploaded, and somebody misses an assessment invitation
with neither side finding out.

Two things therefore have to hold, and this module is about both:

  * the COPY is safe to show a candidate -- no score, no grade, no number, and
    written by the product rather than by a model, so a provider outage cannot
    leave the feed blank on the day it matters most;
  * the WRITE happens where the event happens, so no caller can forget.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import candidate_updates as cu
from app.services import hiring_pipeline as hp

#: The four delivered grades. None of them may appear in feed copy: a stage
#: change tells the candidate WHAT HAPPENED, never what it means about their
#: chances, and "you did well" is a grade written in prose.
_GRADE_WORDS = (
    "highly matching",
    "moderately matching",
    "not matching",
    "matching",
)


# ── The catalogue ────────────────────────────────────────────────────────────

def _every_string():
    for template in cu.TEMPLATES.values():
        yield template.kind, "title", template.title
        yield template.kind, "body", template.body


def test_no_number_reaches_a_candidate_through_the_feed() -> None:
    """This is a client-facing surface, so the no-numbers rule applies in full.

    Swept over the whole catalogue rather than checked at the call site,
    because a rule enforced at one call site is a rule the next entry breaks.
    """
    for kind, field, text in _every_string():
        assert not any(char.isdigit() for char in text), f"{kind}.{field}"


def test_no_grade_word_reaches_a_candidate_through_the_feed() -> None:
    for kind, field, text in _every_string():
        lowered = text.lower()
        for word in _GRADE_WORDS:
            assert word not in lowered, f"{kind}.{field} says {word!r}"


def test_no_em_dash_anywhere_in_the_catalogue() -> None:
    """Built from `chr(8212)` so a repo-wide sweep cannot rewrite the code that
    detects it -- a character class that MATCHES a dash is data, not prose."""
    dash = chr(8212)
    for kind, field, text in _every_string():
        assert dash not in text, f"{kind}.{field}"


def test_every_template_fills_from_the_two_declared_holes_only() -> None:
    """`{job}` and `{company}`, and nothing else.

    A template with more holes than that is a template somebody eventually
    fills from candidate-supplied text, at which point the feed renders
    whatever a resume contained.
    """
    for kind, template in cu.TEMPLATES.items():
        filled = template.body.format(job="X", company="Y", link_id="", job_id="")
        assert "{" not in filled and "}" not in filled, kind


def test_the_copy_is_written_here_and_not_generated() -> None:
    """No model is called anywhere in this service.

    The feed is the surface that exists BECAUSE the email may not arrive, and
    the email is the thing that already depends on a provider. A feed that
    failed the same way at the same time would protect nobody. Asserted over
    the source rather than by a docstring.
    """
    import inspect

    source = inspect.getsource(cu)
    for forbidden in ("llm_router", "invoke_llm", "agent_loop", "prompts"):
        assert forbidden not in source, forbidden


def test_the_kind_a_row_stores_matches_the_template_it_came_from() -> None:
    for kind, template in cu.TEMPLATES.items():
        assert template.kind == kind


# ── Which stages produce an update ───────────────────────────────────────────

def test_every_stage_a_candidate_can_reach_produces_an_update() -> None:
    """A stage with no feed entry is a change the candidate never hears about.

    `sourced` is the one deliberate exception and is asserted separately below,
    so this loop covers everything else including the retired `offered`.
    """
    for status in hp.ALL_STATUSES:
        if status == hp.SOURCED:
            continue
        assert cu.for_stage(status) is not None, status
        assert cu.for_stage(status) in cu.TEMPLATES


def test_sourced_produces_no_update() -> None:
    """A resume landing in a recruiter's databank is not an event the candidate
    caused and not something they can act on.

    What they hear about is the INVITATION, if the recruiter sends one, and
    that has its own kind. Telling somebody "you are in a databank" would be
    both a surprise and outside the recruiter's control.
    """
    assert cu.for_stage(hp.SOURCED) is None


def test_starting_an_assessment_makes_a_feed_entry_even_though_it_mails_nobody() -> None:
    """The two channels differ here, deliberately.

    Mailing somebody about something they just did is noise arriving in their
    inbox. The same fact sitting in a feed answers "did my assessment save?",
    which is the single most common thing a candidate wants to check.
    """
    assert hp.TRANSITION_EMAIL[hp.ASSESSMENT_IN_PROGRESS] is None
    assert cu.for_stage(hp.ASSESSMENT_IN_PROGRESS) == cu.ASSESSMENT_STARTED


def test_an_unknown_stage_returns_none_rather_than_raising() -> None:
    """A stage added to the FSM should not take down every transition in the
    product until somebody remembers this table."""
    assert cu.for_stage("teleported") is None
    assert cu.for_stage(None) is None


# ── Writing one ──────────────────────────────────────────────────────────────

class _Session:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)


@pytest.mark.asyncio
async def test_recording_fills_the_names_into_the_copy() -> None:
    session = _Session()
    link_id = uuid.uuid4()
    row = await cu.record(
        session,
        kind=cu.ASSESSMENT_INVITED,
        candidate_id=uuid.uuid4(),
        job_title="Backend Engineer",
        company_name="Acme Corp",
        link_id=link_id,
        job_id=uuid.uuid4(),
        emailed=True,
    )
    assert "Backend Engineer" in row.body
    assert "Acme Corp" in row.body
    assert row.kind == cu.ASSESSMENT_INVITED
    assert row.emailed is True
    assert row.read_at is None
    assert session.added == [row]


@pytest.mark.asyncio
async def test_a_missing_name_reads_as_a_phrase_not_as_the_word_none() -> None:
    """The names are joined at render time and can legitimately be absent.

    "your application for None at None" is the kind of copy that reaches a real
    person and destroys their confidence in everything else the page says.
    """
    row = await cu.record(
        _Session(), kind=cu.APPLICATION_SUBMITTED, candidate_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
    )
    assert "None" not in row.body


@pytest.mark.asyncio
async def test_a_link_that_needs_an_identifier_it_lacks_is_dropped() -> None:
    """Worse than no link: it renders as an affordance and then 404s."""
    row = await cu.record(
        _Session(), kind=cu.APPLICATION_SUBMITTED, candidate_id=uuid.uuid4()
    )
    assert row.link_path is None

    with_id = await cu.record(
        _Session(),
        kind=cu.APPLICATION_SUBMITTED,
        candidate_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
    )
    assert with_id.link_path.startswith("/portal/applications?application=")


@pytest.mark.asyncio
async def test_every_stored_link_is_relative() -> None:
    """A stored path must never become an off-site link.

    The feed renders it as an href, so an absolute URL would turn the
    candidate's own Updates page into somebody else's redirector. The database
    CHECK is the real guard; this asserts the catalogue never tries.
    """
    for template in cu.TEMPLATES.values():
        if template.link_path is not None:
            assert template.link_path.startswith("/"), template.kind
            assert "://" not in template.link_path, template.kind


@pytest.mark.asyncio
async def test_an_unknown_kind_raises_rather_than_writing_nothing() -> None:
    """A programming error, not a runtime condition.

    Silently dropping it would give the candidate an incomplete feed with
    nothing anywhere recording the gap, which is the failure this whole table
    exists to prevent, reintroduced one level down.
    """
    with pytest.raises(KeyError):
        await cu.record(
            _Session(), kind="not_a_kind", candidate_id=uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_recording_does_not_flush() -> None:
    """The caller owns the transaction.

    This is always part of something larger -- a transition, an application, a
    closure -- and flushing here would split a unit of work that has to succeed
    or fail together.
    """
    session = _Session()
    assert not hasattr(session, "flush")  # the fake offers none, and none is used
    await cu.record(
        session, kind=cu.JOB_CLOSED, candidate_id=uuid.uuid4(), link_id=uuid.uuid4()
    )
    assert len(session.added) == 1


# ── Where the write happens ──────────────────────────────────────────────────

def test_the_transition_chokepoint_writes_the_feed() -> None:
    """Beside the status writes, so no caller can forget.

    Six routes and workers call `apply_transition`. Writing the feed at each of
    them is six places to drift; writing it here is one, and it is the same
    argument the file already makes for the denormalised mirror.
    """
    import inspect

    source = inspect.getsource(hp.apply_transition)
    assert "_record_candidate_update" in source


def test_the_transition_does_not_send_the_email_but_does_write_the_feed() -> None:
    """The distinction that makes the third write acceptable.

    An email needs drafting, a provider, recruiter review and a dispatch, and
    every one of those is a reason for the transition to fail over something
    that is not the transition. A feed row is a plain INSERT of fixed copy with
    no external dependency.
    """
    import inspect

    source = inspect.getsource(hp)
    assert "lifecycle_email" not in source
    assert "dispatch(" not in source
    assert "candidate_updates" in source


def test_a_fresh_application_records_its_own_feed_row() -> None:
    """A new link is created directly rather than through the FSM, so the row
    the FSM writes for every other stage has to be written at that call site.

    The CONVERSION path (a sourced candidate applying) needs nothing: it goes
    through `apply_transition`, which already recorded it.
    """
    import inspect

    from app.api import portal

    source = inspect.getsource(portal.apply_to_job)
    assert "candidate_updates.APPLICATION_SUBMITTED" in source

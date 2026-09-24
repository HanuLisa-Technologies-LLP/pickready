"""The AI-assisted Job SWOT Analysis service (2026-09-13 spec, sections 23-33).

WHAT THESE TESTS ARE FOR
------------------------
The feature's whole claim is that the model drafts and the humans own the
result. Every assertion below is one half of that claim:

    the model can draft                      -> request + run write four sections
    a human can replace what it wrote        -> save() persists and latches
    the model cannot undo a human silently   -> the request refuses, then asks
    a confirmed replacement is recoverable   -> restore_previous() puts it back
    two editors cannot overwrite each other  -> save() refuses a stale version
    a failed generation is a state           -> status=failed, content intact
    generation is dispatched work            -> the request writes `generating`
                                                and hands off; a human save
                                                while it runs wins; a lost run
                                                reads as failed

The dispatch itself is replaced by a recorder here (`dispatched` fixture);
`tests/test_swot_analysis_api.py` runs the real after-commit dispatch and the
real worker body against a database.

NO DATABASE, AND THAT IS DELIBERATE
------------------------------------
These are the rules, and the rules are in the service. A fake session that
records what was added and flushed is enough to run every one of them, which
means they run on a laptop with nothing installed and in CI with no stack.
`tests/test_swot_analysis_authorization.py` covers the gates on the routes.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.job import Job
from app.schemas.assessments import SwotAnalysisSectionsIn
from app.models.job_setup import (
    SWOT_ANALYSIS_EDITED,
    SWOT_ANALYSIS_FAILED,
    SWOT_ANALYSIS_GENERATED,
    SWOT_ANALYSIS_GENERATING,
    SWOT_ANALYSIS_NOT_GENERATED,
    JobSwotAnalysis,
)
from app.services import swot_analysis


DRAFT = {
    "strengths": "A senior brief with a well understood skill profile.",
    "weaknesses": "The must-have list is narrow for the band offered.",
    "opportunities": "Adjacent platform engineers convert into this role.",
    "threats": "Two competitors are hiring the same profile this quarter.",
}


def test_save_payload_requires_the_version_the_editor_loaded():
    with pytest.raises(ValidationError):
        SwotAnalysisSectionsIn.model_validate(DRAFT)
    assert SwotAnalysisSectionsIn.model_validate(
        {**DRAFT, "expected_version": 0}
    ).expected_version == 0


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Enough AsyncSession for this service: one row per model, by hand.

    `swot_analysis` reads exactly two rows (the analysis and the intake) and
    writes one. Faking that is four lines; standing a Postgres up to assert
    "a refusal happens before the model is called" would be four lines and a
    container.
    """

    def __init__(self, analysis=None, intake=None, user=None):
        self.rows = {JobSwotAnalysis: analysis, _IntakeMarker: intake}
        self.added: list[object] = []
        self.flushes = 0
        self.locked_analysis_reads = 0
        self._user = user

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is JobSwotAnalysis:
            if statement._for_update_arg is not None:
                self.locked_analysis_reads += 1
            return FakeResult(self.rows[JobSwotAnalysis])
        return FakeResult(self.rows[_IntakeMarker])

    def add(self, row):
        self.added.append(row)
        if isinstance(row, JobSwotAnalysis):
            self.rows[JobSwotAnalysis] = row

    async def flush(self):
        self.flushes += 1
        row = self.rows[JobSwotAnalysis]
        if row is not None and row.version is None:
            row.version = 0

    async def get(self, model, pk):  # pragma: no cover - not reached here
        return self._user


class _IntakeMarker:
    """Stands in for JobSwotIntake in the fake session's row map."""


#: Long enough for `generation_sufficiency.swot_input_state`.
JD_DOCUMENT = "## Role\n" + " ".join(
    "Own the payments service, its on-call rota and the migrations that move "
    "card traffic between processors without dropping a transaction.".split() * 5
)


def a_job(**overrides) -> Job:
    job = Job(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Backend Engineer",
        jd_json={"role": "Own the payments service.", "skills": ["Python"]},
        jd_markdown=JD_DOCUMENT,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def an_analysis(**overrides) -> JobSwotAnalysis:
    row = JobSwotAnalysis(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        status=SWOT_ANALYSIS_NOT_GENERATED,
        human_edited=False,
        version=0,
        previous_json={},
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture(autouse=True)
def dispatched(monkeypatch):
    """Record what the request hands off instead of registering a commit hook
    on the fake session, which has no transaction to hook."""
    sent: list[tuple[str, list, dict]] = []

    def _record(session, name, *, args=None, kwargs=None):
        sent.append((name, list(args or ()), dict(kwargs or {})))
        return None

    monkeypatch.setattr(swot_analysis, "dispatch_after_commit", _record)
    return sent


async def _generate(session, job, *, confirm_overwrite: bool = False):
    """The two halves the product runs: the request, then the worker body."""
    row, _handle = await swot_analysis.request_generation(
        session, job, confirm_overwrite=confirm_overwrite, requested_by=uuid.uuid4()
    )
    return await swot_analysis.run_generation(
        session, job, confirm_overwrite=confirm_overwrite, requested_version=row.version
    )


@pytest.fixture
def no_model(monkeypatch):
    """Replace the model call with the draft, so these tests assert RULES."""

    async def _draft(session, job):
        return dict(DRAFT)

    monkeypatch.setattr(swot_analysis, "draft", _draft)
    return _draft


# ── The model drafts ────────────────────────────────────────────────────────

async def test_a_first_generation_writes_four_sections_and_says_who_wrote_them(
    no_model,
):
    job = a_job()
    session = FakeSession(analysis=an_analysis())

    row = await _generate(session, job)

    assert row.sections() == DRAFT
    assert row.status == SWOT_ANALYSIS_GENERATED
    assert row.generated_by == "ai"
    assert row.last_generated_at is not None
    assert row.version == 1
    # Nothing claims a human has been here yet.
    assert row.human_edited is False


async def test_a_missing_row_is_created_rather_than_a_missing_resource():
    job = a_job()
    session = FakeSession(analysis=None)

    row = await swot_analysis.get_or_create(session, job)

    assert row.status == SWOT_ANALYSIS_NOT_GENERATED
    assert row.job_id == job.id
    assert row in session.added


# ── The human owns the result ───────────────────────────────────────────────

async def test_a_human_save_persists_and_latches_the_edited_flag():
    job = a_job()
    row = an_analysis(status=SWOT_ANALYSIS_GENERATED, generated_by="ai", version=1, **DRAFT)
    session = FakeSession(analysis=row)
    editor = uuid.uuid4()

    saved = await swot_analysis.save(
        session,
        job,
        {**DRAFT, "strengths": "Rewritten by the recruiter."},
        editor_id=editor,
    )

    assert saved.strengths == "Rewritten by the recruiter."
    assert saved.human_edited is True
    assert saved.status == SWOT_ANALYSIS_EDITED
    assert saved.last_modified_by == editor
    assert saved.version == 2
    assert session.locked_analysis_reads == 1


async def test_first_save_seeds_an_empty_document_at_version_zero():
    job = a_job()
    session = FakeSession(analysis=None)

    saved = await swot_analysis.save(
        session, job, DRAFT, editor_id=uuid.uuid4(), expected_version=0
    )

    assert saved.sections() == DRAFT
    assert saved.version == 1
    assert saved.status == SWOT_ANALYSIS_EDITED
    assert session.locked_analysis_reads >= 2


async def test_a_cleared_section_is_genuinely_cleared():
    """A team that disagrees with a paragraph must be able to delete it. A
    required field would force them to keep the model's wording."""
    job = a_job()
    row = an_analysis(version=1, **DRAFT)
    session = FakeSession(analysis=row)

    saved = await swot_analysis.save(
        session, job, {**DRAFT, "threats": "   "}, editor_id=uuid.uuid4()
    )

    assert saved.threats is None


async def test_a_save_against_a_version_that_moved_is_refused():
    job = a_job()
    row = an_analysis(version=4, **DRAFT)
    session = FakeSession(analysis=row)

    with pytest.raises(swot_analysis.VersionConflict):
        await swot_analysis.save(
            session, job, DRAFT, editor_id=uuid.uuid4(), expected_version=3
        )
    # The refusal wrote nothing.
    assert row.version == 4
    assert row.human_edited is False
    assert session.locked_analysis_reads == 1


async def test_two_editors_cannot_overwrite_one_another():
    job = a_job()
    row = an_analysis(version=0)
    session = FakeSession(analysis=row)
    first = {**DRAFT, "strengths": "First editor's change."}
    second = {**DRAFT, "strengths": "Second editor's change."}

    await swot_analysis.save(
        session, job, first, editor_id=uuid.uuid4(), expected_version=0
    )
    with pytest.raises(swot_analysis.VersionConflict):
        await swot_analysis.save(
            session, job, second, editor_id=uuid.uuid4(), expected_version=0
        )

    assert row.strengths == first["strengths"]
    assert row.version == 1
    assert session.locked_analysis_reads == 2


# ── Regeneration cannot silently destroy human work (section 26, 32) ────────

async def test_regeneration_over_human_edits_is_refused_without_confirmation(
    no_model, monkeypatch
):
    job = a_job()
    row = an_analysis(human_edited=True, status=SWOT_ANALYSIS_EDITED, version=2, **DRAFT)
    session = FakeSession(analysis=row)

    called = {"n": 0}

    async def counting_draft(session_, job_):
        called["n"] += 1
        return dict(DRAFT)

    monkeypatch.setattr(swot_analysis, "draft", counting_draft)

    with pytest.raises(swot_analysis.HumanEditsWouldBeLost):
        await _generate(session, job)

    # The refusal came BEFORE the model call: a confirmation prompt that
    # appears after a thirty-second wait is one nobody reads.
    assert called["n"] == 0
    assert row.sections() == DRAFT
    assert row.version == 2


async def test_a_confirmed_regeneration_snapshots_what_it_replaced(no_model):
    job = a_job()
    human = {**DRAFT, "strengths": "What the recruiter actually wrote."}
    row = an_analysis(
        human_edited=True, status=SWOT_ANALYSIS_EDITED, version=2, **human
    )
    session = FakeSession(analysis=row)

    regenerated = await _generate(session, job, confirm_overwrite=True)

    assert regenerated.strengths == DRAFT["strengths"]
    assert regenerated.previous_json["strengths"] == human["strengths"]
    # The fact that a human has edited this document is history, not state:
    # the next regeneration must ask again.
    assert regenerated.human_edited is True


async def test_the_replaced_version_can_be_put_back(no_model):
    job = a_job()
    human = {**DRAFT, "weaknesses": "The real gap is the on-call rota."}
    row = an_analysis(human_edited=True, version=2, **human)
    session = FakeSession(analysis=row)

    await _generate(session, job, confirm_overwrite=True)
    restored = await swot_analysis.restore_previous(session, job)

    assert restored.weaknesses == human["weaknesses"]
    assert restored.status == SWOT_ANALYSIS_EDITED
    # One undo, not a history: the snapshot is spent.
    assert restored.previous_json == {}
    with pytest.raises(swot_analysis.SwotAnalysisError):
        await swot_analysis.restore_previous(session, job)


async def test_regeneration_over_an_ai_draft_nobody_edited_needs_no_confirmation(
    no_model,
):
    job = a_job()
    row = an_analysis(status=SWOT_ANALYSIS_GENERATED, generated_by="ai", version=1, **DRAFT)
    session = FakeSession(analysis=row)

    regenerated = await _generate(session, job)

    assert regenerated.version == 2
    assert regenerated.previous_json == {}


# ── Failure is a state, never an invented SWOT (section 30) ─────────────────

async def test_a_failed_generation_keeps_the_previous_content_and_says_why(
    monkeypatch,
):
    job = a_job()
    row = an_analysis(status=SWOT_ANALYSIS_GENERATED, generated_by="ai", version=1, **DRAFT)
    session = FakeSession(analysis=row)

    async def failing(session_, job_):
        raise swot_analysis.SwotAnalysisError("The SWOT writer could not be reached.")

    monkeypatch.setattr(swot_analysis, "draft", failing)

    with pytest.raises(swot_analysis.SwotAnalysisError):
        await _generate(session, job)

    assert row.status == SWOT_ANALYSIS_FAILED
    assert row.generation_error == "The SWOT writer could not be reached."
    # Section 30: preserve previously saved content.
    assert row.sections() == DRAFT
    assert row.version == 1


# ── The model's response is parsed, never half-accepted ─────────────────────

def test_a_response_missing_a_section_is_unusable_rather_than_partial():
    import json

    assert swot_analysis._parse(json.dumps(DRAFT)) == DRAFT
    incomplete = {k: v for k, v in DRAFT.items() if k != "threats"}
    assert swot_analysis._parse(json.dumps(incomplete)) is None
    assert swot_analysis._parse(json.dumps({**DRAFT, "threats": "  "})) is None
    assert swot_analysis._parse("not json at all") is None
    assert swot_analysis._parse(json.dumps(["a", "list"])) is None


def test_a_runaway_section_is_capped_rather_than_stored_whole():
    import json

    essay = "x" * (swot_analysis.MAX_SECTION_CHARS + 500)
    parsed = swot_analysis._parse(json.dumps({**DRAFT, "strengths": essay}))
    assert parsed is not None
    assert len(parsed["strengths"]) == swot_analysis.MAX_SECTION_CHARS


# ── What the model is allowed to see (section 24) ───────────────────────────

async def test_the_generator_reads_the_job_and_nothing_a_client_sent():
    job = a_job(
        about_company="We build payment rails.",
        experience_min_years=5,
        experience_max_years=8,
    )
    session = FakeSession(analysis=an_analysis(), intake=None)

    context = await swot_analysis.build_context(session, job)

    assert context["title"] == "Backend Engineer"
    assert context["about_company"] == "We build payment rails."
    assert context["experience"] == "5 to 8 years"
    assert context["skills"] == "Python"


async def test_an_absent_value_is_omitted_rather_than_sent_as_an_empty_label():
    job = a_job(about_company=None)
    session = FakeSession(analysis=an_analysis(), intake=None)

    message = swot_analysis._user_message(await swot_analysis.build_context(session, job))

    assert "Job title: Backend Engineer" in message
    assert "About the company:" not in message


# ── Generation is dispatched work (rule 4, Vivekium release) ────────────────

async def test_the_request_writes_generating_and_hands_off_once(no_model, dispatched):
    job = a_job()
    row = an_analysis(version=3)
    session = FakeSession(analysis=row)
    requester = uuid.uuid4()

    requested, _handle = await swot_analysis.request_generation(
        session, job, confirm_overwrite=False, requested_by=requester
    )

    assert requested.status == SWOT_ANALYSIS_GENERATING
    assert requested.generation_requested_at is not None
    assert requested.version == 3, "asking writes no content and moves no version"
    assert dispatched == [
        (
            "pickready.generate_job_swot",
            [str(job.id)],
            {"confirm_overwrite": False, "requested_by": str(requester), "requested_version": 3},
        )
    ]
    # A double click is one request: a generation already running is returned
    # as it stands, with no second hand-off.
    await swot_analysis.request_generation(
        session, job, confirm_overwrite=False, requested_by=requester
    )
    assert len(dispatched) == 1


async def test_a_thin_jd_is_refused_before_anything_is_written(monkeypatch, dispatched):
    from app.services import generation_sufficiency

    called = {"n": 0}

    async def counting_draft(session_, job_):
        called["n"] += 1
        return dict(DRAFT)

    monkeypatch.setattr(swot_analysis, "draft", counting_draft)
    job = a_job(jd_markdown="## Role\nOwn payments.\n## Skills\n")
    row = an_analysis()
    session = FakeSession(analysis=row)

    with pytest.raises(swot_analysis.SwotInputInsufficient) as refused:
        await swot_analysis.request_generation(
            session, job, confirm_overwrite=False, requested_by=uuid.uuid4()
        )
    assert str(refused.value) == generation_sufficiency.EMPTY_STATE_COPY["swot.jd_too_thin"]
    assert row.status == SWOT_ANALYSIS_NOT_GENERATED
    assert dispatched == [] and called["n"] == 0


async def test_a_human_save_during_generation_wins(no_model):
    """The worker calls the model with no lock held, so a person can save in
    the meantime. Their save moves the version; the generation stands down and
    their words survive."""
    job = a_job()
    row = an_analysis(version=1)
    session = FakeSession(analysis=row)
    requested, _ = await swot_analysis.request_generation(
        session, job, confirm_overwrite=False, requested_by=uuid.uuid4()
    )
    asked_at = requested.version

    human = {**DRAFT, "strengths": "Written by the recruiter while it ran."}
    await swot_analysis.save(session, job, human, editor_id=uuid.uuid4())

    written = await swot_analysis.run_generation(
        session, job, confirm_overwrite=False, requested_version=asked_at
    )
    assert written is None
    assert row.strengths == human["strengths"]
    assert row.status == SWOT_ANALYSIS_EDITED


async def test_a_generation_that_never_reports_back_reads_as_failed_without_a_write():
    from datetime import datetime, timedelta, timezone

    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    row = an_analysis(status=SWOT_ANALYSIS_GENERATING, generation_requested_at=long_ago)

    assert swot_analysis.effective_status(row) == (
        SWOT_ANALYSIS_FAILED,
        swot_analysis.STALE_GENERATION_ERROR,
    )
    assert row.status == SWOT_ANALYSIS_GENERATING, "derived at read time, never written"
    fresh = an_analysis(
        status=SWOT_ANALYSIS_GENERATING, generation_requested_at=datetime.now(timezone.utc)
    )
    assert swot_analysis.effective_status(fresh)[0] == SWOT_ANALYSIS_GENERATING


def test_only_the_teams_content_counts_as_a_saved_swot():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert not swot_analysis.is_saved(None)
    assert not swot_analysis.is_saved(an_analysis())
    assert not swot_analysis.is_saved(
        an_analysis(status=SWOT_ANALYSIS_GENERATED, generated_by="ai", **DRAFT)
    ), "a model draft nobody saved is not the team's SWOT"
    assert swot_analysis.is_saved(
        an_analysis(status=SWOT_ANALYSIS_EDITED, human_edited=True, last_modified_at=now, **DRAFT)
    )
    # A regeneration asked for, or failed, over the team's saved words leaves
    # their words in place: still saved.
    assert swot_analysis.is_saved(
        an_analysis(
            status=SWOT_ANALYSIS_GENERATING, human_edited=True, last_modified_at=now,
            last_generated_at=now - timedelta(days=1), **DRAFT,
        )
    )
    # A confirmed regeneration REPLACED them: the model's draft is not saved.
    assert not swot_analysis.is_saved(
        an_analysis(
            status=SWOT_ANALYSIS_GENERATED, human_edited=True,
            last_modified_at=now - timedelta(days=1), last_generated_at=now, **DRAFT,
        )
    )


async def test_the_prompt_is_given_the_grade_never_the_retired_level():
    job = a_job(assessment_grade="leadership", level="Principal")
    session = FakeSession(analysis=an_analysis(), intake=None)

    message = swot_analysis._user_message(await swot_analysis.build_context(session, job))

    assert "Grade: Leadership" in message
    assert "Principal" not in message

"""The AI-assisted Job SWOT Analysis service (2026-09-13 spec, sections 23-33).

WHAT THESE TESTS ARE FOR
------------------------
The feature's whole claim is that the model drafts and the humans own the
result. Every assertion below is one half of that claim:

    the model can draft                      -> generate() writes four sections
    a human can replace what it wrote        -> save() persists and latches
    the model cannot undo a human silently   -> generate() refuses, then asks
    a confirmed replacement is recoverable   -> restore_previous() puts it back
    two editors cannot overwrite each other  -> save() refuses a stale version
    a failed generation is a state           -> status=failed, content intact

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

from app.models.job import Job
from app.models.job_setup import (
    SWOT_ANALYSIS_EDITED,
    SWOT_ANALYSIS_FAILED,
    SWOT_ANALYSIS_GENERATED,
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
        self._user = user

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is JobSwotAnalysis:
            return FakeResult(self.rows[JobSwotAnalysis])
        return FakeResult(self.rows[_IntakeMarker])

    def add(self, row):
        self.added.append(row)
        if isinstance(row, JobSwotAnalysis):
            self.rows[JobSwotAnalysis] = row

    async def flush(self):
        self.flushes += 1

    async def get(self, model, pk):  # pragma: no cover - not reached here
        return self._user


class _IntakeMarker:
    """Stands in for JobSwotIntake in the fake session's row map."""


def a_job(**overrides) -> Job:
    job = Job(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Backend Engineer",
        jd_json={"role": "Own the payments service.", "skills": ["Python"]},
        jd_markdown="## Role\nOwn the payments service.",
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

    row = await swot_analysis.generate(session, job)

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
        await swot_analysis.generate(session, job)

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

    regenerated = await swot_analysis.generate(session, job, confirm_overwrite=True)

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

    await swot_analysis.generate(session, job, confirm_overwrite=True)
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

    regenerated = await swot_analysis.generate(session, job)

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
        await swot_analysis.generate(session, job)

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

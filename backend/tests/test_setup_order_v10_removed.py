"""The pre-v10 job setup order is gone, and this is what keeps it gone.

WHAT WAS REMOVED (CONTRACT v10, owner ruling 2026-09-28)
--------------------------------------------------------
Until v10 the order was JD, then the Job SWOT, then Skills, and the lock waited
for the first assessment START:

* `skills.after_swot_saved`: the first human SWOT save asked Sutra for the
  skills draft. A SWOT save now drafts NOTHING; the draft starts from the JD
  (`skills.after_jd_saved`, at create and on a JD save with no skill rows).
* `skills.SWOT_NOT_SAVED_DETAIL` ("Save the SWOT first ..."): a draft refused
  without a saved SWOT. The SWOT is optional context now; the draft's gate is
  the JD (`generation_sufficiency.skills_draft_input_state`).
* `api/jobs.PUBLISH_STEP_SWOT` ("save the SWOT analysis"): a publication step.
  Publish needs the JD and saved skills only.
* `assessment_contract.SKILLS_LOCKED_DETAIL` and `api/jobs.GRADE_LOCKED_DETAIL`,
  the two sentences that named a started assessment. The ONE refusal is the
  dated frozen sentence (`assessment_contract.FROZEN_DETAIL`).

A deleted symbol a docstring, a screen or a test still names is a rule one
edit away from returning, so the sweep reads live source whitespace-normalised
(`tests/removal_sweep.py`, the 2026-09-23 lesson).

PENDING, AND IT ONLY SHRINKS: the frontend still hard-codes the retired grade
sentence on the job page. The frontend package of this release replaces it
with the server's `frozen_reason`; each pending file must still HIT, so the
entry is removed in the change that fixes it rather than left as an exemption.
"""
from __future__ import annotations

import pathlib
import re

from tests.removal_sweep import sweep

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

PATTERNS = (
    re.compile(r"\bafter_swot_saved\b"),
    re.compile(r"\bSWOT_NOT_SAVED_DETAIL\b"),
    re.compile(r"Save the SWOT first", re.I),
    re.compile(r"\bPUBLISH_STEP_SWOT\b"),
    re.compile(r"save the SWOT analysis", re.I),
    re.compile(r"\bSKILLS_LOCKED_DETAIL\b"),
    re.compile(r"\bGRADE_LOCKED_DETAIL\b"),
    re.compile(r"skills are locked because a candidate has started", re.I),
    re.compile(r"grade is locked because a candidate has started", re.I),
)

#: Files allowed to name the thing, each with its reason.
ALLOWED = {
    BACKEND / "tests" / "test_setup_order_v10_removed.py": "the sweep itself",
    BACKEND / "tests" / "test_job_skills_draft.py": (
        "asserts `skills` has no `after_swot_saved` attribute: a guard, not a use"
    ),
}

#: Owned by the frontend package of this release (render `frozen_reason`).
PENDING = {
    REPO / "frontend" / "app" / "(org)" / "org" / "jobs" / "[id]" / "page.tsx": (
        "the job page's hard-coded grade lock sentence"
    ),
    REPO / "frontend" / "app" / "(org)" / "org" / "jobs" / "[id]" / "page.test.tsx": (
        "the job page test's copy of the same sentence"
    ),
}


def _hits(pattern: re.Pattern[str]) -> list[str]:
    return sweep(pattern, exempt=[*ALLOWED, *PENDING])


def test_the_pre_v10_setup_order_is_named_nowhere() -> None:
    hits = [hit for pattern in PATTERNS for hit in _hits(pattern)]
    assert not hits, "the pre-v10 setup order is still named:\n" + "\n".join(hits)


def test_every_pending_entry_still_names_it() -> None:
    """A pending entry that no longer hits is an exemption kept open for
    nothing: remove it in the change that fixed the file."""
    stale = [
        str(path.relative_to(REPO))
        for path in PENDING
        if path.exists()
        and not any(
            pattern.search(" ".join(path.read_text(encoding="utf-8").split()))
            for pattern in PATTERNS
        )
    ]
    missing = [str(path.relative_to(REPO)) for path in PENDING if not path.exists()]
    assert not stale and not missing, f"stale pending entries: {stale + missing}"


def test_the_sweep_is_not_vacuous() -> None:
    """The sweep must find the sentence where it is known to be (a pending
    file), or a broken root list would pass everything."""
    known = sweep(PATTERNS[-1], roots=list(PENDING))
    assert known, "the sweep found nothing where the retired sentence is known to be"


def test_the_replacements_are_live() -> None:
    from app.api import jobs
    from app.services import assessment_contract, generation_sufficiency, skills

    assert callable(skills.after_jd_saved)
    assert callable(assessment_contract.freeze_at_application)
    assert "skills.jd_too_thin" in generation_sufficiency.EMPTY_STATE_COPY
    assert jobs.PUBLISH_STEP_JD and jobs.PUBLISH_STEP_SKILLS
    assert not hasattr(jobs, "PUBLISH_STEP_SWOT")
    assert not hasattr(assessment_contract, "SKILLS_LOCKED_DETAIL")

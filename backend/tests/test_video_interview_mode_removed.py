"""The video-interview assessment mode is gone, and this is what keeps it gone.

WHAT WENT, 2026-09-24 (Vivekium release, PLAN-p3 WP5)
-----------------------------------------------------
The second assessment mode, where the recording WAS the answer: its start,
question-mark, whole-file upload, finalize and status routes; their schemas;
the answer-transcription half of `services/video/processing.py` (audio
extraction, Amazon Transcribe over the recording, transcript segmentation by
question marks, the transcript writer, the completion it triggered);
`video/recordings.mark_question_shown`; and `services/assessment_canonical.py`,
the adapter whose only caller was a test. What survives is the proctored
SESSION recording, rebuilt around segmented uploads, and the read-only
constants in `models/dual_mode.py` that let a row written before the removal
still load (`assessment_conversations.mode` and `video_recordings.kind` keep
their CHECK constraints).

WHAT OTHER PACKAGES OF THE SAME RELEASE STILL CARRY
----------------------------------------------------
The mode-selection routes and the per-mode consent text live in the
conversation module (PLAN-p3 WP3), and the mode screen, the video-interview
component, their types and the whole-file upload client live in the frontend
(WP6a, WP6b). Those packages delete them in parallel with this one, so
`PENDING_IN_OTHER_PACKAGES` names the exact files that may still mention
them. It is a RATCHET in both directions, the `test_login_otp_removed.py`
shape: a mention in any other file fails, and an entry whose file no longer
mentions them fails too, so the list can only shrink and is empty when the
release is integrated.
"""
from __future__ import annotations

import ast
import importlib.util
import re

from tests.removal_sweep import BACKEND, REPO, sweep

THIS_FILE = BACKEND / "tests" / "test_video_interview_mode_removed.py"

#: Gone now, everywhere. The route names and schema names of the deleted
#: mode, and every step of its transcription pipeline.
GONE = re.compile(
    r"\bstart_video_interview\b|\bmark_video_question\b"
    r"|\bupload_video_recording\b|\bfinalize_video_interview\b"
    r"|\bmark_question_shown\b|\bwrite_transcript_records\b"
    r"|\bsegment_transcript\b|\bassessment_canonical\b|\bbuild_canonical\b"
    r"|\bVideoStartOut\b|\bVideoMarkIn\b|\bVideoQuestionOut\b"
    r"|\bVideoRecordingStatusOut\b|\bprocess_proctored_session_recording\b"
    r"|\bcompress_store_and_finish\b|\bexpired_recordings\b"
    r"|(?<!in)\bcomplete_assessment\b|\bvideo_interview_transcript\b"
    r"|\bNO_SPOKEN_ANSWER\b"
)

#: Removed by the parallel packages named in the module docstring.
PENDING = re.compile(
    r"\bselect_assessment_mode\b|\bget_assessment_mode\b|\bAssessmentModeIn\b"
    r"|\bModeStateOut\b|\bassessment_consent_text_video\b|\bModeSelection\b"
    r"|\bVideoInterview\w*|\bAssessmentModeState\b"
    r"|assessment/(?:mode-selection|video-interview)\b"
    r"|/video/(?:start|mark|upload|finalize|status)\b"
)

#: EMPTY since the stage 2 integration, which merged every package this list
#: waited on (WP3, WP6a, WP6b). The ratchet reached zero: a pending name in
#: any file now fails, like a GONE one.
PENDING_IN_OTHER_PACKAGES: frozenset[str] = frozenset()

#: The test that asserts the single consent text REPLACED the per-mode one has
#: to name the deleted setting to assert its absence.
PENDING_EXEMPT = (
    THIS_FILE,
    BACKEND / "tests" / "test_single_mode_consent.py",
)


#: The test that asserts the pipeline no longer DEFINES or CALLS the deleted
#: steps has to name them to do it, the same reason this file is exempt.
GONE_EXEMPT = (
    THIS_FILE,
    BACKEND / "tests" / "test_assessment_media_storage.py",
)


def _files(hits: list[str]) -> set[str]:
    return {hit.split(":", 1)[0].replace("\\", "/") for hit in hits}


def test_the_deleted_modules_are_gone() -> None:
    assert importlib.util.find_spec("app.services.assessment_canonical") is None
    assert not (BACKEND / "app" / "services" / "assessment_canonical.py").exists()
    assert not (BACKEND / "tests" / "test_dual_mode_assessment.py").exists()


def test_nothing_names_what_was_removed() -> None:
    hits = sweep(GONE, exempt=GONE_EXEMPT)
    assert not hits, hits


def test_the_pending_names_do_not_spread() -> None:
    hits = sweep(PENDING, exempt=PENDING_EXEMPT)
    spread = sorted(_files(hits) - PENDING_IN_OTHER_PACKAGES)
    assert not spread, (
        "A deleted video-interview name appeared outside the files its owning "
        f"package is still removing it from. Remove it rather than listing it: {spread}"
    )


def test_the_pending_list_only_shrinks() -> None:
    """An entry whose file no longer mentions the names is an exemption that
    outlived its reason. Delete the entry in the change that removed it."""
    stale = sorted(PENDING_IN_OTHER_PACKAGES - _files(sweep(PENDING, exempt=PENDING_EXEMPT)))
    assert not stale, stale


def test_the_recording_routes_are_the_session_recording_only() -> None:
    """The recording module serves the proctored session's recording and the
    staff retry, and nothing that selects, marks or transcribes a mode."""
    from app.api import assessment_recording

    paths = sorted(route.path for route in assessment_recording.router.routes)
    assert paths == sorted(
        [
            "/conversations/links/{link_id}/session-media/start",
            "/conversations/{conversation_id}/recording/segments",
            "/conversations/{conversation_id}/recording/segments/{segment_id}/parts/{part_number}",
            "/conversations/{conversation_id}/recording/segments/{segment_id}/complete",
            "/conversations/{conversation_id}/recording/finalize",
            "/conversations/{conversation_id}/recording/status",
            "/videos/recordings/{recording_id}/retry",
        ]
    )


def test_nothing_writes_a_mode_outside_the_pending_files() -> None:
    """No module assigns `<conversation>.mode`, so every new conversation
    carries the column default and the deleted mode cannot be selected again.
    The conversation module's mode route is on the pending list until its
    package removes it."""
    offenders: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel in PENDING_IN_OTHER_PACKAGES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "mode":
                        offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, offenders


def test_no_assessment_route_accepts_a_mode() -> None:
    """The other half of "nothing writes a mode": no assessment route takes a
    request body carrying a `mode` field, so a client cannot ask for the
    deleted mode either. Read from the mounted application rather than from
    source text, so a route added under any name is covered. The outreach
    compose `mode` (email versus message) is a different word and is not an
    assessment route. The one route still carrying it lives in a file on the
    pending list, and its entry goes when that package deletes it."""
    import inspect
    import pathlib

    from fastapi.routing import APIRoute

    from app.main import app

    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/assessments" not in route.path:
            continue
        source = pathlib.Path(inspect.getsourcefile(route.endpoint)).resolve()
        rel = source.relative_to(REPO).as_posix()
        for param in route.dependant.body_params:
            fields = getattr(param.field_info.annotation, "model_fields", {})
            if "mode" in fields and rel not in PENDING_IN_OTHER_PACKAGES:
                offenders.append(f"{sorted(route.methods)} {route.path} ({rel})")
    assert not offenders, offenders


def test_the_old_rows_still_have_their_vocabulary() -> None:
    """Deleting the WRITERS must not delete the READ vocabulary: a row written
    before the removal carries `video_interview` and must still load and be
    labelled for what it was."""
    from app.models.dual_mode import (
        ASSESSMENT_MODES,
        MODE_VIDEO_INTERVIEW,
        RECORDING_KINDS,
        RECORDING_VIDEO_INTERVIEW,
    )
    from app.services import assessment_video_access

    assert MODE_VIDEO_INTERVIEW in ASSESSMENT_MODES
    assert RECORDING_VIDEO_INTERVIEW in RECORDING_KINDS
    assert assessment_video_access.mode_label(MODE_VIDEO_INTERVIEW) == "Video interview"

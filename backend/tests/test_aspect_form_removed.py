"""The 40-aspect questionnaire and its outreach routes are gone, and stay gone.

WHAT WAS REMOVED (2026-09-24, WP6-B)
------------------------------------
`ASPECT_DEFINITIONS` was thirty-five placeholder prompts ("Aspect n") and a
databank consent, served by `GET/POST /portal/outreach/{token}` behind a
signed token that only `POST /verification/outreach` minted, with a public
page that also demanded age and gender. Nothing live needed any of it: the
profile form on My Profile replaced the questionnaire on 2026-07-27, and the
six validation fields replaced it on the application on 2026-07-30. There is
now ONE apply path, `POST /portal/jobs/{id}/apply`, for the portal board and
the public `/apply/{job}` page alike.

HOW THIS SWEEP WORKS
--------------------
The `test_company_dna_removed.py` pattern: the source is swept with whitespace
normalised, so a name wrapped across a line still matches, and a hit still
names its line. The scope is the live tree: `backend/app`, `backend/scripts`,
`frontend/app`, `frontend/components`, `frontend/lib` and `frontend/proxy.ts`.

THE REMOVAL CROSSES FOUR WORK PACKAGES, SO THE SWEEP CARRIES A RATCHET.
`PENDING` names each file another package still has to clean, with its owner.
A hit in any other file fails. A PENDING entry whose file no longer hits fails
as STALE, so a cleaned file cannot keep its allowance for a new hit to reuse.
The list must be EMPTY by the end of the release.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]

#: The retired feature's names, and the phrases that described it as live.
PATTERN = re.compile(
    r"ASPECT_DEFINITIONS|_PERSONAL_ASPECTS|AspectsForm|aspects-form|AspectOut\b"
    r"|OutreachInfoOut|OutreachSubmitOut|portal/outreach|verification/outreach"
    r"|make_outreach_token|decode_outreach_token|OUTREACH_TOKEN_TTL_DAYS"
    r"|candidate_outreach|40-aspect|40 aspects?\b|40-question",
    re.I,
)

SCOPE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/app", (".py",)),
    ("backend/scripts", (".py", ".sh")),
    ("frontend/app", (".ts", ".tsx")),
    ("frontend/components", (".ts", ".tsx")),
    ("frontend/lib", (".ts", ".tsx")),
)
SINGLE_FILES = ("frontend/proxy.ts",)

#: Files another work package still has to clean, and who. MUST SHRINK TO
#: EMPTY. Paths are repository-relative with forward slashes.
PENDING: dict[str, str] = {
    "backend/app/api/verification.py": "WP6-C: delete send_outreach and its imports",
    "backend/app/api/deps.py": (
        "orchestrator (WP6-A hunk 8): delete make_outreach_token, "
        "decode_outreach_token and OUTREACH_TOKEN_TTL_DAYS with the last caller"
    ),
    "backend/app/services/email_render.py": (
        "orchestrator: delete the candidate_outreach default in the merge that "
        "removes verification.send_outreach, its only sender"
    ),
    "backend/app/api/candidates.py": "WP6-D: the /status route and its 40-question refusal",
    "backend/app/schemas/candidates.py": "WP6-D: ProfileOut docstring",
    "backend/app/api/jobs.py": "Phase 1: databank upload docstring (orchestrator hunk)",
    "backend/app/services/capabilities.py": "orchestrator hunk: SEND_OUTREACH comment",
    "backend/app/services/assessment_invite.py": "orchestrator hunk: token comment",
    "backend/app/services/resume_prefill.py": "Phase 3 deletes the module",
    "backend/app/scripts/validate_stack.py": "orchestrator hunk: comment",
    "backend/app/scripts/seed_mock_data.py": "orchestrator hunk: comment",
    "backend/app/scripts/seed_demo_applications.py": "orchestrator hunk: comment",
    "frontend/app/(candidate)/portal/outreach/[token]/page.tsx": "WP6-E: delete the page",
    "frontend/app/apply/[job_uuid]/page.tsx": "WP6-E: the public page uses ApplyForm",
    "frontend/components/aspects-form.tsx": "WP6-E: delete",
    "frontend/lib/aspects.test.ts": "WP6-E: delete with lib/aspects.ts",
    "frontend/lib/auth-context.tsx": "WP6-E: drop the public-route entry",
    "frontend/proxy.ts": "WP6-E: drop the public-route entry",
}

#: Routes that must not be served. The second waits on WP6-C.
GONE_ROUTES = ("/api/v1/portal/outreach/{token}",)
PENDING_ROUTES = {"/api/v1/verification/outreach": "WP6-C: delete send_outreach"}

THIS_FILE = pathlib.Path(__file__).resolve()


def _flatten(text: str) -> tuple[str, list[int]]:
    """Whitespace runs collapsed to one space, with each kept character's
    offset in the original, so a match across a line break still names a line.
    """
    flat: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            if flat and flat[-1] == " ":
                continue
            flat.append(" ")
        else:
            flat.append(character)
        offsets.append(index)
    return "".join(flat), offsets


def hits_in(text: str) -> list[tuple[int, str]]:
    flat, offsets = _flatten(text)
    return [
        (text.count("\n", 0, offsets[match.start()]) + 1, match.group(0))
        for match in PATTERN.finditer(flat)
    ]


def _files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root, suffixes in SCOPE:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix in suffixes
                and "node_modules" not in path.parts
                and "__pycache__" not in path.parts
                and path.resolve() != THIS_FILE
            ):
                found.append(path)
    for single in SINGLE_FILES:
        if (REPO / single).is_file():
            found.append(REPO / single)
    return found


def _sweep() -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for path in _files():
        found = hits_in(path.read_text(encoding="utf-8"))
        if found:
            result[path.relative_to(REPO).as_posix()] = found
    return result


def test_the_sweep_has_something_to_sweep() -> None:
    """A sweep over an empty file list passes for ever."""
    assert len(_files()) > 100


def test_no_live_source_names_the_retired_questionnaire() -> None:
    found = _sweep()
    unexpected = {
        path: lines for path, lines in found.items() if path not in PENDING
    }
    assert not unexpected, (
        "the retired 40-aspect questionnaire or its outreach routes are named in "
        f"live source: {unexpected}"
    )
    stale = sorted(set(PENDING) - set(found))
    assert not stale, (
        f"these files no longer name the retired feature; remove them from "
        f"PENDING so the ratchet stays tight: {stale}"
    )


def test_the_sweep_matches_across_a_line_break() -> None:
    """The blind spot `test_company_dna_removed` once had, one line wide."""
    sample = "a = 1\n# the 40\n    aspects were\nb = make_outreach_token\n"
    assert hits_in(sample) == [(2, "40 aspects"), (4, "make_outreach_token")]


def test_the_routes_are_not_served() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    for route in GONE_ROUTES:
        assert route not in paths, f"{route} is still served"
    stale = sorted(route for route in PENDING_ROUTES if route not in paths)
    assert not stale, (
        f"these routes are gone; remove them from PENDING_ROUTES: {stale}"
    )


def test_the_portal_module_holds_none_of_it() -> None:
    """A retained definition is what makes a new route cheap."""
    from app.api import portal
    from app.schemas import portal as portal_schemas

    for name in ("ASPECT_DEFINITIONS", "_PERSONAL_ASPECTS", "MAX_EMPLOYER_EMAILS",
                 "outreach_info", "outreach_submit", "_outreach_context"):
        assert not hasattr(portal, name), name
    for name in ("AspectOut", "OutreachInfoOut", "OutreachSubmitOut"):
        assert not hasattr(portal_schemas, name), name


def test_the_apply_route_takes_only_the_one_form() -> None:
    """The retired fields cannot come back as parameters: FastAPI would bind
    them, and a bound field is a field somebody eventually writes."""
    import inspect

    from app.api import portal

    parameters = set(inspect.signature(portal.apply_to_job).parameters)
    assert parameters == {
        "job_id", "resume", "reuse_previous", "validation",
        "application_source", "user", "session",
    }, parameters

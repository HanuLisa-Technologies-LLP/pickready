"""`resume-file` must redirect to a route that EXISTS (2026-08-09).

Reported as "docx resumes show but pdf ones do not". It was neither about the
format nor about storage.

`resume_file` answers the first, token-less request with a 307 back to itself
carrying a short-lived access token. That target was written out by hand as
`/api/v2/candidates/profiles/{id}/resume-file`, and the `candidates` router is
mounted at `/api/v1` ONLY. So every resume view and every download 307ed to a
path that does not exist and 404ed, for every profile and every format, from
the moment private storage made this endpoint the only way to read a file.

It presented as a FORMAT bug purely because of which endpoint each format uses:
a DOCX is rendered by `resume-preview`, which has no redirect and worked fine,
so only PDFs visibly failed. That is why the tests below assert the property
that was actually violated -- the redirect target resolves to a mounted route --
rather than asserting a particular prefix string, which is the same mistake in
a different place.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app.api import candidates as candidates_api
from app.main import app
from app.models.enums import Role


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, profile, link):
        self._profile = profile
        self._link = link

    async def get(self, _model, _pk):
        return self._profile

    async def execute(self, _query):
        return _Result([self._link] if self._link is not None else [])


def _request(path: str):
    """Just enough of a Request for the handler: it reads `url.path` only."""
    return SimpleNamespace(url=SimpleNamespace(path=path))


def _caller(tenant_id: uuid.UUID):
    from app.api import deps
    from app.core.security import AUDIENCE_ORG

    return deps.CurrentUser(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=Role.hr_manager,
        audience=AUDIENCE_ORG,
    )


@pytest.fixture
def granted(monkeypatch):
    async def _has_capability(*_args, **_kwargs):
        return True

    monkeypatch.setattr(candidates_api.rbac, "has_capability", _has_capability)


def _mounted_paths() -> set[str]:
    """Every path the app actually SERVES, taken from its OpenAPI schema.

    Not `app.routes`: this FastAPI version keeps included routers as opaque
    wrapper objects with an empty `path`, so walking that list silently reports
    only the four built-in doc routes and would make every assertion below pass
    vacuously. The schema is the same surface the deployed service publishes.
    """
    return set(app.openapi()["paths"])


async def _redirect_for(path: str, granted_fixture=None):
    profile_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    profile = SimpleNamespace(
        id=profile_id,
        resume_url="gs://pickready-resumes-private/resumes/abc",
        resume_original_filename="MyResume.pdf",
        resume_mime_type="application/pdf",
    )
    link = SimpleNamespace(profile_id=profile_id, tenant_id=tenant_id,
                           hm_access_granted=True)
    return await candidates_api.resume_file(
        request=_request(path.format(profile_id=profile_id)),
        profile_id=profile_id,
        download=False,
        access_token=None,
        user=_caller(tenant_id),
        session=_Session(profile, link),
    ), profile_id


# ── The defect ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_redirect_target_is_a_route_this_app_actually_serves(granted):
    """The property that was violated. Asserted against the app's OWN mount
    table, so it stays true if the prefix ever moves again."""
    response, profile_id = await _redirect_for(
        "/api/v1/candidates/profiles/{profile_id}/resume-file"
    )
    target = urlparse(response.headers["location"]).path
    template = target.replace(str(profile_id), "{profile_id}")
    assert template in _mounted_paths(), f"{target} is not a mounted route"


@pytest.mark.asyncio
async def test_the_redirect_stays_on_the_prefix_it_was_called_through(granted):
    """Derived from the request, never written out by hand. A hardcoded
    `/api/v2/...` here 404ed every resume read in production."""
    response, profile_id = await _redirect_for(
        "/api/v1/candidates/profiles/{profile_id}/resume-file"
    )
    location = response.headers["location"]
    assert location.startswith(
        f"/api/v1/candidates/profiles/{profile_id}/resume-file?"
    )
    assert "/api/v2/" not in location


@pytest.mark.asyncio
async def test_it_would_follow_the_prefix_if_the_router_were_mounted_elsewhere(
    granted,
):
    """The regression is impossible rather than merely fixed: mount the router
    anywhere and the redirect follows it."""
    response, profile_id = await _redirect_for(
        "/api/v9/candidates/profiles/{profile_id}/resume-file"
    )
    assert response.headers["location"].startswith("/api/v9/candidates/")


@pytest.mark.asyncio
async def test_the_redirect_carries_the_token_and_preserves_download(granted):
    """The whole reason the round trip exists. Losing either turns the second
    request into a 403 or silently flips a download into an inline view."""
    response, _ = await _redirect_for(
        "/api/v1/candidates/profiles/{profile_id}/resume-file"
    )
    location = response.headers["location"]
    assert "access_token=" in location
    assert "download=false" in location
    assert response.status_code == 307
    # A resume URL must never be cached by a shared proxy.
    assert response.headers["Cache-Control"] == "private, no-store"


# ── Why it looked like a format bug ──────────────────────────────────────────

def test_both_resume_endpoints_are_mounted_under_the_same_prefix() -> None:
    """A DOCX goes to `resume-preview` and a PDF to `resume-file`. That is the
    only reason a broken URL presented as a broken FORMAT, so the two staying
    together is worth pinning."""
    paths = _mounted_paths()
    preview = {p for p in paths if p.endswith("/resume-preview")}
    fetch = {p for p in paths if p.endswith("/resume-file")}
    assert preview and fetch
    assert {p.rsplit("/", 1)[0] for p in preview} == {
        p.rsplit("/", 1)[0] for p in fetch
    }

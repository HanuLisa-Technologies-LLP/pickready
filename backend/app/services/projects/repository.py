"""Public-repository ingestion. Public only, by product decision: no OAuth,
no token intake from candidates, no private access path exists to widen.

The provider registry is keyed by host so a second public provider is a table
row plus a fetcher, not a rewrite; GitHub is the one implemented. Nothing from
the repository is ever executed: the tree is inspected FIRST, generated and
dependency paths are excluded before a byte of content is fetched, and only a
bounded set of meaningful files is downloaded, each under its own size cap.

Fetched content is repository DATA, never instructions (the same rule resume
chunks follow through conversation_guardrails downstream).
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.services.projects.formats import (
    FAMILY_ARCHIVE,
    FAMILY_IMAGE,
    FAMILY_UNSUPPORTED,
    classify,
    is_ignored_path,
)
from app.services.projects.limits import ProjectLimits

_FETCH_TIMEOUT = 30.0


class RepositoryRejected(RuntimeError):
    """The URL is not an acceptable public repository. Candidate-safe reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RepositoryUnavailable(RuntimeError):
    """The provider could not serve the repository right now. Retryable."""


@dataclass(frozen=True)
class RepositoryRef:
    provider: str
    host: str
    owner: str
    name: str
    url: str


@dataclass
class RepositoryFetch:
    ref: RepositoryRef
    #: Provider metadata kept as provenance: default branch, description,
    #: last-push timestamp, declared size. Facts about the repository, not
    #: assertions about the candidate.
    metadata: dict[str, Any]
    #: (path, bytes) for each meaningful file fetched.
    files: list[tuple[str, bytes]]
    #: Tree accounting for telemetry and honesty about reduction.
    total_tree_entries: int = 0
    ignored_entries: int = 0
    skipped_oversize: int = 0
    limitations: list[str] = field(default_factory=list)


#: host -> provider key. Adding a provider: add the host here and implement a
#: fetcher in `_FETCHERS` below.
SUPPORTED_HOSTS: dict[str, str] = {
    "github.com": "github",
    "www.github.com": "github",
}


def validate_repository_url(url: str) -> RepositoryRef:
    """Accept exactly `https://<supported host>/<owner>/<repo>`.

    Credentials embedded in the URL are refused outright: this pipeline
    handles public repositories only, and a URL carrying a token is a secret
    the candidate should never have been asked for.
    """
    raw = (url or "").strip()
    if not raw:
        raise RepositoryRejected("No repository URL was provided.")
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise RepositoryRejected("Repository links must start with https.")
    if parsed.username or parsed.password:
        raise RepositoryRejected(
            "Repository links must not contain credentials. Only public "
            "repositories are supported."
        )
    host = (parsed.hostname or "").lower()
    provider = SUPPORTED_HOSTS.get(host)
    if provider is None:
        supported = ", ".join(sorted(set(SUPPORTED_HOSTS) - {"www.github.com"}))
        raise RepositoryRejected(
            f"That repository host is not supported yet. Supported: {supported}."
        )
    segments = [s for s in (parsed.path or "").split("/") if s]
    if len(segments) < 2:
        raise RepositoryRejected(
            "The link must point at a repository, for example "
            "https://github.com/owner/project."
        )
    owner, name = segments[0], segments[1]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise RepositoryRejected("The repository owner or name is missing.")
    return RepositoryRef(
        provider=provider,
        host="github.com" if provider == "github" else host,
        owner=owner,
        name=name,
        url=f"https://{host}/{owner}/{name}",
    )


# ── File selection ───────────────────────────────────────────────────────────

#: Lower number fetches first. The ordering encodes what the master brief
#: calls "meaningful files": manifests and docs establish identity and stack,
#: CI/docker establish engineering practice, source establishes implementation.
def _priority(path: str) -> int:
    lowered = path.lower()
    name = posixpath.basename(lowered)
    if name in {"readme.md", "readme.rst", "readme.txt", "readme"}:
        return 0
    cls = classify(path)
    if cls.family == "manifest":
        return 1
    if "/.github/workflows/" in f"/{lowered}" or name.endswith(
        (".gitlab-ci.yml", "azure-pipelines.yml")
    ):
        return 2
    if cls.family == "document":
        return 3
    if cls.family == "source_code":
        depth = lowered.count("/")
        return 4 + min(depth, 4)
    if cls.family in {"structured_data", "notebook", "cad", "spreadsheet"}:
        return 9
    return 12


def select_tree_paths(
    entries: list[dict[str, Any]], limits: ProjectLimits
) -> tuple[list[dict[str, Any]], int, int]:
    """Choose which tree entries are worth fetching.

    Returns (chosen entries, ignored count, oversize count). Deterministic:
    same tree, same selection.
    """
    ignored = 0
    oversize = 0
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        if is_ignored_path(path):
            ignored += 1
            continue
        cls = classify(path)
        if cls.family in {FAMILY_IMAGE, FAMILY_ARCHIVE, FAMILY_UNSUPPORTED}:
            ignored += 1
            continue
        size = int(entry.get("size") or 0)
        if size > limits.repo_max_file_bytes:
            oversize += 1
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: (_priority(str(e["path"])), str(e["path"])))
    return candidates[: limits.repo_max_files], ignored, oversize


# ── GitHub fetcher ───────────────────────────────────────────────────────────


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ReadyPick-ProjectEvidence",
    }
    token = (get_settings().github_api_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_github(response: httpx.Response, ref: RepositoryRef) -> None:
    if response.status_code == 404:
        raise RepositoryRejected(
            "The repository could not be found. Check the link and make sure "
            "the repository is public."
        )
    if response.status_code in {403, 429}:
        raise RepositoryUnavailable(
            f"github rate limited fetching {ref.owner}/{ref.name}"
        )
    if response.status_code >= 400:
        raise RepositoryUnavailable(
            f"github answered {response.status_code} for {ref.owner}/{ref.name}"
        )


async def _fetch_github(ref: RepositoryRef, limits: ProjectLimits) -> RepositoryFetch:
    headers = _github_headers()
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, headers=headers) as client:
        meta_response = await client.get(
            f"https://api.github.com/repos/{ref.owner}/{ref.name}"
        )
        _raise_for_github(meta_response, ref)
        meta = meta_response.json()
        if meta.get("private"):
            raise RepositoryRejected("Only public repositories are supported.")
        branch = str(meta.get("default_branch") or "main")

        tree_response = await client.get(
            f"https://api.github.com/repos/{ref.owner}/{ref.name}"
            f"/git/trees/{branch}?recursive=1"
        )
        _raise_for_github(tree_response, ref)
        tree = tree_response.json()
        entries = list(tree.get("tree") or [])
        chosen, ignored, oversize = select_tree_paths(entries, limits)

        limitations: list[str] = []
        if tree.get("truncated"):
            limitations.append(
                "The repository tree was too large for the provider to list "
                "in full; evidence covers the listed portion only."
            )
        if len(entries) and not chosen:
            limitations.append(
                "No parsable files were identified in the repository tree."
            )

        files: list[tuple[str, bytes]] = []
        for entry in chosen:
            path = str(entry["path"])
            raw = await client.get(
                f"https://raw.githubusercontent.com/{ref.owner}/{ref.name}"
                f"/{branch}/{path}"
            )
            if raw.status_code != 200:
                limitations.append(f"One file could not be fetched: {path}")
                continue
            content = raw.content[: limits.repo_max_file_bytes + 1]
            if len(content) > limits.repo_max_file_bytes:
                limitations.append(f"One file exceeded the fetch limit: {path}")
                continue
            files.append((path, content))

        return RepositoryFetch(
            ref=ref,
            metadata={
                "provider": ref.provider,
                "url": ref.url,
                "default_branch": branch,
                "description": (meta.get("description") or "")[:400],
                "pushed_at": meta.get("pushed_at"),
                "declared_size_kb": meta.get("size"),
                "fork": bool(meta.get("fork")),
                "tree_sha": (tree.get("sha") or "")[:40],
            },
            files=files,
            total_tree_entries=len(entries),
            ignored_entries=ignored,
            skipped_oversize=oversize,
            limitations=limitations,
        )


_FETCHERS = {
    "github": _fetch_github,
}


async def fetch_repository(ref: RepositoryRef, limits: ProjectLimits) -> RepositoryFetch:
    """Fetch metadata plus the bounded meaningful-file set for a public repo."""
    fetcher = _FETCHERS.get(ref.provider)
    if fetcher is None:
        raise RepositoryRejected("That repository provider is not supported yet.")
    try:
        return await fetcher(ref, limits)
    except httpx.HTTPError as exc:
        raise RepositoryUnavailable(
            f"repository fetch failed: {type(exc).__name__}"
        ) from exc

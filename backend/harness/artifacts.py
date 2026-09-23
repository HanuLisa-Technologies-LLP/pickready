"""Where a run's evidence lands, and why the store is bounded.

WHY BOUNDED RETENTION IS A DESIGN REQUIREMENT AND NOT A TIDINESS PREFERENCE
----------------------------------------------------------------------------
HARNESS.md section 5: "Retention is bounded, `--keep-last N` defaulting to 20,
because an artifact store that grows without limit is one somebody eventually
deletes wholesale." That sentence describes an observed failure mode rather than
a hypothetical. The run a team most needs is the one from the morning the
unbounded directory was finally cleared out.

Which is why `prune` refuses to touch a promoted baseline whatever its age. A
baseline is the reference a regression is measured against (section 8), so
deleting it does not merely lose an artifact, it silently disables the
comparison: `compare` would then report "no baseline" and a caller reading only
the exit code would see the gate go quiet rather than red.

THE STORE OWNS THE FILE LOCATIONS, `baseline.py` OWNS THE SEMANTICS
---------------------------------------------------------------------
`baselines.json` sits in this root, so this module knows its PATH and can read
the promoted ids it must protect. What promotion MEANS, which run is eligible,
and how a comparison is scored all live in `baseline.py`. Splitting it the other
way round would force this module to import `baseline` and `baseline` to import
this one.

A RUN DIRECTORY WITHOUT A MANIFEST IS AN ERROR, NOT AN EMPTY RUN
------------------------------------------------------------------
`list_runs` raises on one rather than skipping it. Skipping is the silent
fallback rule in miniature: a half written run directory is evidence that
something died, and a listing that quietly omitted it would hide exactly the run
worth looking at.

Provenance: docs/spec/HARNESS.md section 5.
"""
from __future__ import annotations

import json
import pathlib
import shutil
from dataclasses import dataclass
from typing import Any, Collection, Mapping

from harness.run import RunRecord

#: Directory name at the repository root. Gitignored: a run artifact is
#: reproducible evidence of one execution, and committing it would put a stale
#: report where the next reader takes it for the current one, which is the
#: argument `.gitignore` already makes about `backend/coverage.json`.
ARTIFACT_DIRNAME = "harness-runs"

#: The one manifest filename. Named as a constant because `list_runs`,
#: `begin` and `baseline` all resolve it, and three literals would be three
#: chances to rename two of them.
MANIFEST_NAME = "manifest.json"
BASELINES_NAME = "baselines.json"

#: Default retention, from HARNESS.md section 5.
DEFAULT_KEEP_LAST = 20


class ArtifactError(RuntimeError):
    """The artifact store cannot do what was asked, with the path named."""


def repo_root() -> pathlib.Path:
    """The repository root, resolved from this file rather than from the cwd.

    `harness.sh` runs from the repository root and the CLI is normally invoked
    from `backend/`, so a cwd-relative root would put artifacts in two places
    depending on how the harness was started. Two artifact stores is the same
    defect as two answers to any other question: the disagreement is silent.
    """
    return pathlib.Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RunIndexEntry:
    """One row of `harness list`, read from the manifest rather than the filesystem.

    Sorting by `started_at` from the manifest rather than by directory mtime is
    deliberate: an mtime moves when a report is rewritten, so an mtime ordering
    reshuffles history every time somebody re-renders a report.
    """

    run_id: str
    started_at: str
    scenario_id: str
    scenario_version: int
    tier: str
    status: str
    path: pathlib.Path
    promoted: bool


class ArtifactStore:
    """Run artifacts under `<repo>/harness-runs/`, one directory per run."""

    def __init__(self, root: pathlib.Path | str | None = None) -> None:
        self.root = (
            pathlib.Path(root)
            if root is not None
            else repo_root() / ARTIFACT_DIRNAME
        )

    # ── Locations ────────────────────────────────────────────────────────────

    @property
    def baselines_path(self) -> pathlib.Path:
        return self.root / BASELINES_NAME

    def run_dir(self, run_id: str) -> pathlib.Path:
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise ArtifactError(
                f"{run_id!r} is not a usable run id. A run id names a directory, "
                "so a separator in one would write outside the store."
            )
        return self.root / run_id

    def path_for(self, run_id: str, name: str) -> pathlib.Path:
        return self.run_dir(run_id) / name

    # ── Writing ──────────────────────────────────────────────────────────────

    def begin(self, run: RunRecord) -> pathlib.Path:
        """Create the run's directory and write its manifest before anything executes.

        The manifest is written FIRST, on purpose. A run that is killed mid
        scenario, which is the ordinary outcome of the failure a harness exists
        to find, then still leaves behind the commit it stood on, the scenario
        version, the faults and the seed. A manifest written at the end is a
        manifest that exists only for runs that did not need one.
        """
        directory = self.run_dir(run.run_id)
        if directory.exists():
            raise ArtifactError(
                f"{directory} already exists. A run id collision would overwrite "
                "another run's evidence."
            )
        directory.mkdir(parents=True)
        self.write_manifest(run)
        return directory

    def write_manifest(self, run: RunRecord) -> pathlib.Path:
        return self.write_json(run.run_id, MANIFEST_NAME, run.to_manifest())

    def write_text(self, run_id: str, name: str, data: str) -> pathlib.Path:
        path = self.path_for(run_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def write_json(self, run_id: str, name: str, data: Any) -> pathlib.Path:
        return self.write_text(
            run_id, name, json.dumps(data, indent=2, sort_keys=True, default=str) + "\n"
        )

    # ── Reading ──────────────────────────────────────────────────────────────

    def read_json(self, run_id: str, name: str) -> Any:
        path = self.path_for(run_id, name)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactError(f"{path}: cannot be read: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactError(f"{path}: is not valid JSON: {exc}") from exc

    def read_run(self, run_id: str) -> RunRecord:
        body = self.read_json(run_id, MANIFEST_NAME)
        if not isinstance(body, Mapping):
            raise ArtifactError(
                f"{self.path_for(run_id, MANIFEST_NAME)}: manifest is not an object"
            )
        try:
            return RunRecord.from_manifest(body)
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactError(
                f"{self.path_for(run_id, MANIFEST_NAME)}: manifest cannot be read "
                f"back into a run: {exc}"
            ) from exc

    def promoted_run_ids(self) -> frozenset[str]:
        """The run ids pinned as baselines, read from this store's own registry.

        An absent `baselines.json` means nothing has been promoted yet, which is
        a real and common state rather than a failed read: the file is created
        by the first promotion. A file that exists and cannot be parsed is a
        different matter and raises, because silently treating it as empty would
        make `prune` eligible to delete every baseline it was meant to protect.
        """
        if not self.baselines_path.exists():
            return frozenset()
        try:
            body = json.loads(self.baselines_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactError(
                f"{self.baselines_path}: cannot be read: {exc}. Refusing to "
                "continue, because an unreadable baseline registry would let a "
                "prune delete the runs it exists to protect."
            ) from exc
        if not isinstance(body, Mapping):
            raise ArtifactError(f"{self.baselines_path}: is not an object")
        ids: set[str] = set()
        for entry in body.values():
            if isinstance(entry, Mapping) and isinstance(entry.get("run_id"), str):
                ids.add(entry["run_id"])
        return frozenset(ids)

    def list_runs(self) -> tuple[RunIndexEntry, ...]:
        """Every run in the store, newest first."""
        if not self.root.is_dir():
            return ()
        promoted = self.promoted_run_ids()
        entries: list[RunIndexEntry] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            manifest = directory / MANIFEST_NAME
            if not manifest.exists():
                raise ArtifactError(
                    f"{directory}: has no {MANIFEST_NAME}. A run directory "
                    "without a manifest is evidence that a run died before it "
                    "could record itself, not a run to skip over. Remove the "
                    "directory deliberately if that is what you mean."
                )
            body = self.read_json(directory.name, MANIFEST_NAME)
            entries.append(
                RunIndexEntry(
                    run_id=str(body.get("run_id") or directory.name),
                    started_at=str(body.get("started_at") or ""),
                    scenario_id=str(body.get("scenario_id") or ""),
                    scenario_version=int(body.get("scenario_version") or 0),
                    tier=str(body.get("tier") or ""),
                    status=str(body.get("status") or ""),
                    path=directory,
                    promoted=directory.name in promoted,
                )
            )
        # Newest first by the manifest's own timestamp; the run id breaks a tie
        # so the order is TOTAL. An unstable order paginates and diffs badly,
        # the same argument `job_candidates.order_by_clause` already makes.
        return tuple(
            sorted(entries, key=lambda item: (item.started_at, item.run_id), reverse=True)
        )

    # ── Retention ────────────────────────────────────────────────────────────

    def prune(
        self, keep_last: int = DEFAULT_KEEP_LAST, *, protected: Collection[str] = ()
    ) -> tuple[str, ...]:
        """Delete the oldest runs beyond `keep_last`, never a promoted baseline.

        A promoted run does not consume one of the `keep_last` slots. Counting it
        would mean that promoting a baseline quietly shortened the history by
        one, and after twenty promotions the store would hold nothing else.

        Returns the ids actually deleted, so a caller can say what happened. A
        prune that reports nothing is indistinguishable from one that ran
        against the wrong directory.
        """
        if keep_last < 1:
            raise ArtifactError(
                f"keep_last must be at least 1, got {keep_last}. A store keeping "
                "zero runs deletes the run that has just finished, including the "
                "failing one somebody is about to read."
            )
        kept = set(protected) | set(self.promoted_run_ids())
        deleted: list[str] = []
        remaining = 0
        for entry in self.list_runs():
            if entry.run_id in kept:
                continue
            remaining += 1
            if remaining <= keep_last:
                continue
            shutil.rmtree(entry.path)
            deleted.append(entry.run_id)
        return tuple(deleted)

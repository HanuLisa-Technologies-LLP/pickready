#!/usr/bin/env python
"""Enforce per-package line AND branch coverage floors (spec-doc6 §11.1).

    "Unit tests for every new module, at >=90% line and branch coverage for
     app/hiring/, app/miti/, app/siddhi/, the Company DNA package and the RBAC
     authorization layer. Coverage floors enforced in CI, per-package, not
     global."

PER-PACKAGE, AND WHY THE WORD IS LOAD-BEARING
----------------------------------------------
A global floor is satisfiable by a large, well-covered surface carrying a small
untested one, and the small untested one is always the module that shipped last
week. This codebase is roughly 260 modules; a single new package at 20% moves a
global figure by less than a percentage point and would never be seen.

BRANCH AS WELL AS LINE, FOR THE SAME REASON THE HARD CAP IS A `min`
--------------------------------------------------------------------
Line coverage records that `if any_must_have_is_not_matching:` executed. Branch
coverage records that BOTH arms did. The arms of a band cap, a gate refusal and
an evidence-sufficiency exclusion are exactly where this product's defects have
lived, and a line-only floor is blind to every one of them.

USAGE
-----
    pytest -q --cov --cov-report=json:coverage.json
    python scripts/check-coverage-floors.py

or point it elsewhere with `--json <path>`. Exit code 0 when every package
meets its floor, 1 otherwise, printing every package rather than only the
failures -- a gate that prints only what broke gives a reader no way to see how
close the rest are.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: The floor spec-doc6 §11.1 sets. One number for both line and branch: the
#: spec states ">=90% line and branch" without splitting them, and splitting
#: them here would be inventing a policy the document does not have.
FLOOR = 90.0


@dataclass(frozen=True)
class Package:
    """One floor. `prefixes` are POSIX paths relative to `backend/`."""

    name: str
    prefixes: tuple[str, ...]
    why: str


PACKAGES: tuple[Package, ...] = (
    Package(
        name="hiring",
        prefixes=("app/services/hiring/",),
        why="Layers 1 to 3, the weight derivation, the gates and the Runbook data",
    ),
    Package(
        name="miti",
        prefixes=("app/services/miti/",),
        why="the five isolated dimension evaluators and the deterministic aggregator",
    ),
    Package(
        name="siddhi",
        prefixes=("app/services/siddhi/",),
        why="architectural citation enforcement and the no-numbers rule",
    ),
    Package(
        name="company_dna",
        # The Company DNA package is not one directory in this repository. The
        # instrument and the compiler sit under `hiring/`, the route and the
        # schema beside their peers. Naming all four here is what makes the
        # floor a floor on the FEATURE rather than on whichever directory
        # happened to be created for it.
        prefixes=(
            "app/services/hiring/company_dna.py",
            "app/services/hiring/dna_compilation.py",
            "app/api/company_dna.py",
            "app/schemas/company_dna.py",
        ),
        why="the twelve-section intake, the observable-evidence detector and the compiler",
    ),
    Package(
        name="rbac",
        prefixes=(
            "app/services/rbac.py",
            "app/services/capabilities.py",
            "app/services/role_hierarchy.py",
        ),
        why="authorization, tenant isolation and role ownership",
    ),
    Package(
        name="vendor_reliability",
        # Added by the vendor-verification work. Every path in it exists to
        # behave correctly during a failure, so an untested branch here is an
        # untested failure mode by definition.
        prefixes=(
            "app/services/reliability/",
            "app/services/llm_router.py",
            "app/config/llm_providers.py",
            "app/services/embeddings.py",
        ),
        why="the router, the contract checks and the embedding client",
    ),
)


@dataclass
class Measurement:
    package: Package
    statements: int = 0
    missing: int = 0
    branches: int = 0
    partial: int = 0
    files: int = 0

    @property
    def line_percent(self) -> float:
        if not self.statements:
            return 0.0
        return 100.0 * (self.statements - self.missing) / self.statements

    @property
    def branch_percent(self) -> float:
        if not self.branches:
            # No branches at all is not a failure. It is a package with no
            # conditionals in it, and reporting 0% would be reporting the
            # absence of a thing as a shortfall.
            return 100.0
        return 100.0 * (self.branches - self.partial) / self.branches

    @property
    def ok(self) -> bool:
        return (
            self.files > 0
            and self.line_percent >= FLOOR
            and self.branch_percent >= FLOOR
        )


def _normalise(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def measure(report: dict[str, object]) -> list[Measurement]:
    files = report.get("files")
    if not isinstance(files, dict):
        raise SystemExit(
            "The coverage JSON has no 'files' section. Produce it with "
            "`pytest --cov --cov-report=json:coverage.json`."
        )

    results = [Measurement(package=p) for p in PACKAGES]
    for raw_path, data in files.items():
        path = _normalise(str(raw_path))
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        for result in results:
            if not any(path.startswith(prefix) for prefix in result.package.prefixes):
                continue
            result.files += 1
            result.statements += int(summary.get("num_statements", 0))
            result.missing += int(summary.get("missing_lines", 0))
            result.branches += int(summary.get("num_branches", 0))
            result.partial += int(summary.get("num_partial_branches", 0))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-package coverage floors, line and branch."
    )
    parser.add_argument(
        "--json",
        default=str(BACKEND / "coverage.json"),
        help="the coverage JSON report (default: backend/coverage.json)",
    )
    args = parser.parse_args(argv)

    report_path = pathlib.Path(args.json)
    if not report_path.exists():
        print(
            f"No coverage report at {report_path}. Run:\n"
            f"  pytest -q --cov --cov-report=json:coverage.json",
            file=sys.stderr,
        )
        return 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    results = measure(report)

    width = max(len(m.package.name) for m in results)
    print(f"Per-package coverage floors, {FLOOR:.0f}% line and branch\n")
    print(
        f"{'PACKAGE'.ljust(width)}  {'FILES'.rjust(5)}  {'LINE'.rjust(7)}  "
        f"{'BRANCH'.rjust(7)}  RESULT"
    )
    print(f"{'-' * width}  {'-' * 5}  {'-' * 7}  {'-' * 7}  ------")
    failures: list[Measurement] = []
    for m in results:
        verdict = "pass" if m.ok else "FAIL"
        if not m.ok:
            failures.append(m)
        print(
            f"{m.package.name.ljust(width)}  {m.files:5d}  "
            f"{m.line_percent:6.2f}%  {m.branch_percent:6.2f}%  {verdict}"
        )

    if not failures:
        print("\nEvery package meets its floor.")
        return 0

    print("")
    for m in failures:
        if m.files == 0:
            print(
                f"{m.package.name}: NO FILES MATCHED. The paths in this "
                f"script have drifted from the tree, which is a silent pass "
                f"waiting to happen: {', '.join(m.package.prefixes)}"
            )
            continue
        print(
            f"{m.package.name} ({m.package.why}) is below the floor: "
            f"line {m.line_percent:.2f}%, branch {m.branch_percent:.2f}%."
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

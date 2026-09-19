"""The Intercom integration is gone, and this is what keeps it gone.

WHY A TEST FOR AN ABSENCE
---------------------------
A vendor integration removed by unregistering its task while its client module
survives is one import away from coming back, and it comes back quietly:
somebody finds a working, documented, allowlisted CRM client on disk and wires
it to the next thing that wants a customer record. This file asserts the MODULE
is gone, the SETTING is gone and the SCHEDULE is gone, because each of the
three would separately make a revival cheap.

WHAT REPLACED IT, AND WHAT DID NOT CHANGE
-------------------------------------------
`services/support`, an in-product ticket surface. The reason the Intercom
allowlist existed did not go away with the vendor: a support conversation is
about the CUSTOMER, and candidate data must never appear in one. What changed
is that the boundary is now a schema boundary rather than an outbound payload
allowlist, which is strictly stronger, because there is no projection left to
widen. `test_support_candidate_boundary.py` is where that rule now lives.

WHAT IS DELIBERATELY NOT SWEPT
--------------------------------
`docs/verification/VERIFICATION_RESULTS.md` still names the EventBridge rule,
because it is a dated record of a deployment on which that rule really was
enabled. Provenance is not updated to match current behaviour.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

GONE = ("app.services.intercom",)


@pytest.mark.parametrize("module", GONE)
def test_the_module_cannot_be_imported(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__(module)


@pytest.mark.parametrize("module", GONE)
def test_the_file_is_not_on_disk_either(module: str) -> None:
    """An import failing is not the same as a file being gone. A module left on
    disk but unreachable is a file the next reader finds and assumes is live."""
    assert not (REPO / (module.replace(".", "/") + ".py")).exists()


def test_the_task_is_not_registered() -> None:
    """A registered task is dispatchable by name whether or not anything
    schedules it, so unregistering is the half that actually removes reach."""
    from app.workers import registry

    names = set(registry.names())
    assert "pickready.sync_intercom_companies" not in names


def test_no_schedule_entry_survives() -> None:
    from app.workers import schedule

    assert "readypick-sync-intercom-companies" not in schedule.RULE_NAMES


def test_the_setting_is_gone() -> None:
    """Left in place it would read as a supported capability that is merely
    unconfigured, which is exactly the state the deleted module reported."""
    from app.core.config import Settings

    assert "intercom_access_token" not in Settings.model_fields


def test_no_executable_source_still_names_the_vendor() -> None:
    """Swept over the tree rather than checked at a call site, because a rule
    enforced at one call site is a rule the next file breaks."""
    pattern = re.compile(r"intercom", re.I)
    allowed = {REPO / "tests" / "test_intercom_removed.py"}
    offending: list[str] = []
    for root in ("app", "tests", "scripts"):
        for path in (REPO / root).rglob("*.py"):
            if path in allowed or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").split("\n"), 1
            ):
                if pattern.search(line):
                    offending.append(
                        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
                    )
    assert not offending, offending


def test_no_infrastructure_still_declares_the_rule() -> None:
    """The Python half and the Terraform half are two places, and this codebase
    has already shipped a schedule that existed in one and not the other."""
    infra = REPO.parent / "infra" / "environments"
    offending: list[str] = []
    for path in infra.rglob("*.tf"):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").split("\n"), 1
        ):
            if re.search(r"intercom", line, re.I):
                offending.append(f"{path.name}:{number}: {line.strip()}")
    assert not offending, offending

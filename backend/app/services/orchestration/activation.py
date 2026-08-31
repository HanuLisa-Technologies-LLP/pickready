"""Which module implements which pipeline stage, resolved late and refused loudly.

WHY THE IMPORTS ARE LAZY AND THE REFUSAL IS EXPLICIT
------------------------------------------------------
Part A is activated stage by stage. While that is in progress, this package has
to be importable against a tree where some stages have landed and others have
not, and there are exactly two ways to write that. One is a `try: import ...
except ImportError: <something else>`, which is the silent fallback spec-doc6
4.1 forbids by name and which would let a stage run on the module it was meant
to replace while every log line said otherwise. The other is to resolve the
module at the moment the stage needs it and to REFUSE, naming the module and the
work that is missing, when it is not there.

This is the second one. `load` raises `StageModuleMissing` with the dotted name
and the sentence a person can act on. Nothing here substitutes, degrades, or
falls back.

WHY A TABLE RATHER THAN AN IMPORT AT EACH CALL SITE
-----------------------------------------------------
Because "which module is Miti" then has one answer instead of one per caller,
and because `status()` can report the activation frontier without importing
anything: a stage's absence is a fact about the tree, and asking for it should
not cost an import of everything that HAS landed.

THIS MODULE NEVER IMPORTS `hiring`, `miti` OR `siddhi` AT MODULE SCOPE
------------------------------------------------------------------------
Deliberate, and `test_import_graph` would catch it if it stopped being true.
Those packages import the service layer, the service layer imports orchestration
in places, and a module-scope import here would close a cycle that surfaces as
`AttributeError: partially initialized module` in whichever order production
happens to import things -- a failure this codebase has already had twice and
which is invisible to a full-suite run because some earlier module has already
finished initialising the target.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from app.services.agents import provenance

__all__ = [
    "StageModule",
    "StageModuleMissing",
    "STAGE_MODULES",
    "load",
    "status",
    "symbol",
]


class StageModuleMissing(RuntimeError):
    """A stage's implementation has not landed, or has lost a symbol.

    A `RuntimeError` rather than an `ImportError` on purpose: an `ImportError`
    invites a caller to catch it and carry on, and there is nothing to carry on
    to. The message names the module, the symbols expected and the work that
    supplies them, because the only useful response is to go and finish that
    work.
    """


@dataclass(frozen=True)
class StageModule:
    """The module one pipeline stage is implemented by, and what it must expose.

    `symbols` is not documentation. A module that imports but has lost the
    function a stage calls is the more dangerous of the two failures, because
    the import succeeds and the `AttributeError` surfaces several frames away
    from the cause, usually inside a generative step.
    """

    dotted: str
    symbols: tuple[str, ...]
    #: The activation work that supplies it, in the words a person would use to
    #: go and look for it. "Miti has not been activated" is actionable; "no
    #: module named app.services.miti.pipeline" is a stack trace.
    supplied_by: str


#: One entry per stage that Part A activation must supply. Stages absent from
#: this table (`job_created`, `application`, `human_disposition`) are database
#: facts rather than agent implementations, and have no module to resolve.
STAGE_MODULES: dict[str, StageModule] = {
    provenance.STAGE_SWOT: StageModule(
        "app.services.hiring.swot_quality",
        ("__name__",),
        "Bodha's SWOT session and its 18.5 quality-control rejection rules",
    ),
    provenance.STAGE_MATRIX: StageModule(
        "app.services.hiring.scorecard",
        ("require_frozen_matrix", "load_frozen_matrix", "freeze"),
        "Sutra's seven-stage transformation and the frozen scorecard (gate G1)",
    ),
    provenance.STAGE_PRESCREEN: StageModule(
        "app.services.hiring.prescreen",
        ("PreScreenResult",),
        "Yukti's resume-stage pre-screen grade",
    ),
    provenance.STAGE_SCORING: StageModule(
        "app.services.miti.pipeline",
        ("__name__",),
        "Miti's five isolated dimension evaluators and the deterministic aggregator",
    ),
    provenance.STAGE_REPORT: StageModule(
        "app.services.siddhi.synthesis",
        ("compose",),
        "Siddhi's PRISM synthesis with architectural citation enforcement",
    ),
    #: The kill switch. Not a stage of its own; it is checked at the top of
    #: every stage, and it is listed here so its absence is refused with the
    #: same sentence as anything else rather than discovered as an ImportError.
    "pipeline_halt": StageModule(
        "app.services.hiring.pipeline_halt",
        ("check", "enforce", "PipelineHalted"),
        "the RPN_PIPELINE_HALT kill switch",
    ),
}


def load(stage: str) -> ModuleType:
    """Import the module implementing `stage`, or refuse by name.

    No caching. `importlib` already caches in `sys.modules`, and a second cache
    here would hold a module the tree no longer has -- which during an
    activation is the difference between a red test and a green one that proves
    nothing.
    """
    try:
        spec = STAGE_MODULES[stage]
    except KeyError as exc:
        raise StageModuleMissing(
            f"stage {stage!r} has no declared implementation module; "
            f"declared stages are {sorted(STAGE_MODULES)}"
        ) from exc
    try:
        module = importlib.import_module(spec.dotted)
    except ImportError as exc:
        raise StageModuleMissing(
            f"stage {stage!r} needs {spec.dotted!r}, which is not importable. "
            f"It is supplied by {spec.supplied_by}. Nothing substitutes for it: "
            "a stage that ran on the module it was meant to replace would "
            f"report {stage!r} as complete while doing the old thing."
        ) from exc
    missing = [name for name in spec.symbols if not hasattr(module, name)]
    if missing:
        raise StageModuleMissing(
            f"{spec.dotted!r} imports but does not expose {missing}, which "
            f"stage {stage!r} calls. Supplied by {spec.supplied_by}."
        )
    return module


def symbol(stage: str, name: str) -> Any:
    """One attribute off a stage's module, with the same refusal on absence."""
    module = load(stage)
    if not hasattr(module, name):
        spec = STAGE_MODULES[stage]
        raise StageModuleMissing(
            f"{spec.dotted}.{name} does not exist; stage {stage!r} calls it. "
            f"Supplied by {spec.supplied_by}."
        )
    return getattr(module, name)


def status() -> dict[str, dict[str, Any]]:
    """Which stages have landed and which have not, without importing them.

    Used by the end-to-end journey test to SKIP with a precise reason naming the
    missing stage, rather than failing in a way that reads as a regression in
    somebody else's work, and by `eval_agents.py` to report the activation
    frontier.
    """
    import importlib.util

    report: dict[str, dict[str, Any]] = {}
    for stage, spec in STAGE_MODULES.items():
        try:
            found = importlib.util.find_spec(spec.dotted) is not None
        except (ImportError, ValueError):
            # A parent package that does not import is not a present module.
            found = False
        report[stage] = {
            "module": spec.dotted,
            "present": found,
            "supplied_by": spec.supplied_by,
        }
    return report


def missing_stages() -> tuple[str, ...]:
    """Stages whose module is not on disk yet, in pipeline order."""
    present = status()
    return tuple(
        stage
        for stage in provenance.STAGES
        if stage in present and not present[stage]["present"]
    )

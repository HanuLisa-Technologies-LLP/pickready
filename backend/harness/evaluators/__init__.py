"""Resolving the evaluator names a scenario asks for.

WHY AN UNKNOWN NAME IS AN `unavailable` OUTCOME AND NOT A CRASH
-----------------------------------------------------------------
A scenario naming an evaluator that does not exist has asked a question nobody
can answer, which is exactly what `unavailable` means. Raising would lose the
rest of the run's evidence over a typo in one line of YAML, and skipping would
be worse: the run would report a pass on the evaluators that did resolve while
silently answering fewer questions than the scenario asked. The outcome carries
the registry's contents, so the fix is in the message.

WHY THE REGISTRY IS ASSEMBLED HERE RATHER THAN DECLARED IN ONE MODULE
-----------------------------------------------------------------------
`builtin.py` holds the nine of HARNESS.md section 7, which share a shape:
they read the run and answer a dimension. `release.py` is separate because it
wires in a module that had no caller at all, and that argument is worth its own
file rather than a paragraph inside a list of nine.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Mapping

from harness.run import EvaluationOutcome

from .base import EvaluationInput, cannot_compute
from .builtin import EVALUATORS as _BUILTIN
from .release import release_gate_contract

__all__ = ["EvaluationInput", "evaluate", "registered"]

Evaluator = Callable[[EvaluationInput], Awaitable[EvaluationOutcome]]

_REGISTRY: Mapping[str, Evaluator] = {
    **_BUILTIN,
    "release_gate": release_gate_contract,
}


def registered() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


async def evaluate(name: str, data: EvaluationInput) -> EvaluationOutcome:
    """Run one named evaluator, or report that the name resolves to nothing."""
    evaluator = _REGISTRY.get(name.strip())
    if evaluator is None:
        return cannot_compute(
            name.strip() or "unnamed",
            f"no evaluator is registered under {name.strip()!r}. The registry "
            f"holds {', '.join(registered())}.",
        )
    return await evaluator(data)

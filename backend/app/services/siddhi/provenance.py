"""What Siddhi tells the provenance record, and nothing about how it is kept.

A STRUCTURAL INTERFACE, deliberately, rather than a class Siddhi owns. The one
recorder a scoring run carries is threaded through Miti AND Siddhi, and Miti
may not import Siddhi (the pipeline runs one way). So the concrete recorder
lives with the pipeline's shared value types (`assessment_pipeline/types.py`)
and Siddhi states only the two calls it makes. Anything with these two methods
satisfies it; a test passes a recorder of its own.

THE RULE BOTH METHODS SERVE (CLAUDE.md, 2026-09-10, report provenance)
-----------------------------------------------------------------------
`functional_skills_reports.model_id` and `prompt_version` must never claim a
model call that did not happen. So a model call is recorded ONLY after the call
returned a value the deterministic critic ACCEPTED, and a fixed template is
recorded as a template, by where it was used. A report whose every remark and
probe came from a template therefore carries no model id at all, and one whose
remarks were written by a model but whose probes fell back names the model and
lists the templates beside it. Either way the reader is told what produced the
text, which is what the columns exist for.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["ProvenanceSink"]


@runtime_checkable
class ProvenanceSink(Protocol):
    """The two facts Siddhi records about how a piece of report text was made."""

    def model_call(self, task_type: str, prompt_name: str | None) -> None:
        """A model wrote this text and the critic accepted it.

        `prompt_name` is the registry name of the versioned prompt the call
        rendered, or None when the prompt is inline in the caller (the remark
        system prompt is, and is versioned by the image rather than the
        registry).
        """

    def template(self, where: str) -> None:
        """This text is a fixed fallback template, used at `where`."""

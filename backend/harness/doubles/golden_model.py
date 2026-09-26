"""The model, scripted at the ROUTER, for the golden journey (CONTRACT v4 item 1).

WHY THE ROUTER AND NOT THE VENDOR TRANSPORT
---------------------------------------------
`harness.faults.model_answers` serves an answer at the httpx seam, below the
router, and it is the right double for a scenario about ONE call. The golden
journey reaches a dozen task types in one run, and the vendor request carries
no task type: a transport double would have to guess which call it was
answering from the prompt text, and a guess that answers the wrong call with a
plausible shape is the silent fallback rule 6 forbids, moved into the harness.
`llm_router.invoke_llm(task_type, messages, ...)` is the ONE chokepoint every
product call reaches (claude.md, 2026-08-05) and it names the task, so the
script is keyed by the name the product itself uses.

WHAT IS REAL AROUND IT
------------------------
Everything the router's caller does with the answer: the agent loop, the
deterministic evaluators (Sutra's validator, the question rubric check, Miti's
band parser, Siddhi's citation chokepoint), and every write. The double only
decides what the model SAID.

AN UNSCRIPTED CALL IS RECORDED AND REFUSED, NEVER ANSWERED
------------------------------------------------------------
A call for a task this script does not know raises `LLMUnavailableError`, the
product's own outage signal, so the caller degrades exactly as its contract
says, AND the call is kept in `unscripted`. The journey asserts that list is
empty: a caller that degraded silently would otherwise read as a pass.

Every answer is built FROM the request (the skills it names, the question it
asks, the evidence ids it offers), never from the world, so an answer names
what the product actually sent.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Sequence

__all__ = ["GoldenModel", "UnscriptedTask"]


class UnscriptedTask(KeyError):
    """A task type this script has no answer for."""


Answer = Callable[["GoldenModel", Sequence[Mapping[str, Any]]], Any]


def _user_payload(messages: Sequence[Mapping[str, Any]]) -> Any:
    """The last user message, parsed as JSON when it is JSON."""
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            try:
                return json.loads(content)
            except ValueError:
                return content
    return None


def _all_text(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _first_sentence(text: str) -> str:
    match = re.search(r"[^.!?]+[.!?]", text or "")
    return (match.group(0) if match else (text or "")).strip()


# ── Job setup ────────────────────────────────────────────────────────────────


def _skills_draft(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("skills_drafting was asked without a JSON payload")
    weakness = _first_sentence(str((request.get("swot") or {}).get("weaknesses") or ""))
    must: list[dict[str, Any]] = [{"name": "Python", "source": "jd"}]
    if weakness:
        must.append(
            {"name": "Payments reconciliation", "source": "swot", "swot_quote": weakness}
        )
    return {
        "must_have": must,
        "nice_to_have": [{"name": "PostgreSQL tuning", "source": "jd"}],
        "behavioural": [{"name": "Ownership under pressure", "source": "company"}],
    }


def _assessment_context(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    skills = request.get("skills") if isinstance(request, Mapping) else None
    if not isinstance(skills, list) or not skills:
        raise UnscriptedTask("assessment_context was asked for no skills")
    priorities: dict[str, int] = {}
    written = []
    for entry in skills:
        bucket, name = str(entry["bucket"]), str(entry["name"])
        priorities[bucket] = priorities.get(bucket, 0) + 1
        written.append(
            {
                "bucket": bucket,
                "name": name,
                "evidence_line": (
                    f"Has shipped production work that depended on {name} and "
                    "explained the decisions made along the way."
                ),
                "priority": priorities[bucket],
            }
        )
    return {
        "role_summary": (
            "Owns the payments platform and its services end to end, from "
            "design review to the on-call rota."
        ),
        "skills": written,
        "refused": [],
    }


def _extraction(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    return {
        "skills": ["Python", "PostgreSQL", "Payments reconciliation"],
        "total_experience_years": 6,
        "education": [],
        "employment_history": [],
    }


# ── Yukti (AI Match) ─────────────────────────────────────────────────────────


def _yukti(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """One reading per candidate, every quote lifted verbatim from the resume
    the request carried, so grounding keeps it rather than refusing it."""
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("yukti_matching was asked without a JSON payload")
    results = []
    for candidate in request.get("candidates") or []:
        quote = _first_sentence(str(candidate.get("resume") or ""))
        results.append(
            {
                "candidate": candidate["ref"],
                "skills": [
                    {"skill": skill["ref"], "verdict": "strong", "quote": quote}
                    for skill in request.get("skills") or []
                ],
                "experience_level": {
                    "verdict": "strong",
                    "quote": quote,
                    "tag": "Seasoned settlement engineer",
                },
                "role_fit": {
                    "verdict": "strong",
                    "quote": quote,
                    "tag": "Payments platform ownership",
                },
                "company_needs": [
                    {
                        "need": need["ref"],
                        "verdict": "some",
                        "quote": quote,
                        "tag": "Reconciliation ownership",
                    }
                    for need in request.get("needs") or []
                ],
            }
        )
    return {"results": results}


#: task type -> how the model answers it. Extended one entry per task the
#: journey reaches; a task missing here is refused and recorded.
SCRIPT: dict[str, Answer] = {
    "skills_drafting": _skills_draft,
    "assessment_context": _assessment_context,
    "yukti_matching": _yukti,
    "extraction": _extraction,
}


@dataclass
class GoldenModel:
    """The scripted router. `calls` is every task answered, in order."""

    script: Mapping[str, Answer] = field(default_factory=lambda: dict(SCRIPT))
    calls: list[str] = field(default_factory=list)
    unscripted: list[str] = field(default_factory=list)

    def answer(self, task_type: str, messages: Sequence[Mapping[str, Any]]) -> str:
        handler = self.script.get(task_type)
        if handler is None:
            raise UnscriptedTask(task_type)
        value = handler(self, messages)
        self.calls.append(task_type)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    @contextmanager
    def installed(self) -> Iterator["GoldenModel"]:
        """Swap `invoke_llm` at the router and at the two modules that bound
        the name at import, and put every one back on the way out."""
        from app.services import llm_router, web_research  # noqa: PLC0415
        from app.services.rag import contextual  # noqa: PLC0415

        async def _invoke(task_type: str, messages: list[dict[str, Any]], *args: Any, **kwargs: Any) -> str:
            try:
                raw = self.answer(task_type, messages)
            except UnscriptedTask as exc:
                self.unscripted.append(str(exc.args[0]) if exc.args else task_type)
                raise llm_router.LLMUnavailableError(
                    f"the golden journey scripts no answer for {task_type}"
                ) from exc
            validate = kwargs.get("validate")
            if validate is not None:
                validate(raw)
            return raw

        targets = (llm_router, web_research, contextual)
        previous = [(module, module.invoke_llm) for module in targets]
        for module in targets:
            module.invoke_llm = _invoke
        try:
            yield self
        finally:
            for module, original in previous:
                module.invoke_llm = original

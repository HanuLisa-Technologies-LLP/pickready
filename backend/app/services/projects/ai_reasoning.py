"""The ONE reasoning call per project, and the validation of what comes back.

The model is the reasoning layer, never the parser (master brief section 12):
it receives the reduced evidence pack `evidence.build_evidence_pack` produced
and returns an interpretation. That interpretation is stored SEPARATELY from
the deterministic record, because a model inference must never read as
extracted fact.

Validation here is deterministic code, not another model call: the strength
word must come from the fixed vocabulary (the same words the database CHECK
enforces), the assessment labels must come from the careful-language list, and
no numeric score may appear anywhere. An invalid response raises; the pipeline
records the project `partially_processed` with the deterministic evidence
intact, which is the honest partial-success state rather than a silent
degrade.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.prompts import registry
from app.services import llm_router

TASK_TYPE = "project_evidence"

STRENGTH_VOCABULARY: frozenset[str] = frozenset(
    {"Strong", "Moderate", "Limited", "Insufficient"}
)
ASSESSMENT_VOCABULARY: frozenset[str] = frozenset(
    {
        "strongly supported",
        "partially supported",
        "insufficient evidence",
        "not substantiated by available artifacts",
    }
)

#: A rating-shaped number: "7/10", "85%", "score: 8". Plain counts inside
#: evidence text are legitimate; a score reaching prose a client could read is
#: not, and this is the pattern the guard refuses on.
_SCORE_PATTERN = re.compile(r"\b\d+\s*(?:/\s*10|/\s*100|%)|score\s*[:=]\s*\d", re.I)


class ProjectReasoningError(RuntimeError):
    """The reasoning call failed or returned an unusable interpretation."""


def _clean_str_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]


def validate_interpretation(payload: Any) -> dict[str, Any]:
    """Deterministic validation of the model's JSON. Raises on anything that
    breaks a vocabulary or the no-numbers rule; drops malformed entries."""
    if not isinstance(payload, dict):
        raise ProjectReasoningError("The interpretation is not a JSON object.")

    strength = str(payload.get("evidence_strength") or "").strip().title()
    if strength not in STRENGTH_VOCABULARY:
        raise ProjectReasoningError(
            f"evidence_strength {strength!r} is outside the fixed vocabulary."
        )

    assessments: list[dict[str, Any]] = []
    for entry in payload.get("claim_assessments") or []:
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim") or "").strip()
        label = str(entry.get("assessment") or "").strip().lower()
        if not claim:
            continue
        if label not in ASSESSMENT_VOCABULARY:
            raise ProjectReasoningError(
                f"claim assessment label {label!r} is outside the careful-language vocabulary."
            )
        assessments.append(
            {
                "claim": claim[:500],
                "supporting_evidence": _clean_str_list(entry.get("supporting_evidence")),
                "limiting_evidence": _clean_str_list(entry.get("limiting_evidence")),
                "assessment": label,
            }
        )

    synthesis = str(payload.get("synthesis") or "").strip()[:2500]
    if not synthesis:
        raise ProjectReasoningError("The interpretation carries no synthesis.")

    interpretation = {
        "claim_assessments": assessments,
        "synthesis": synthesis,
        "meaningful_gaps": _clean_str_list(payload.get("meaningful_gaps")),
        "validation_areas": _clean_str_list(payload.get("validation_areas")),
        "evidence_strength": strength,
    }
    rendered = json.dumps(interpretation)
    if _SCORE_PATTERN.search(rendered):
        raise ProjectReasoningError(
            "The interpretation contains a rating-shaped number."
        )
    return interpretation


async def interpret(evidence_pack: str) -> dict[str, Any]:
    """Run the reasoning call and return the validated interpretation.

    Raises `ProjectReasoningError` (invalid output) or the router's own
    `LLMUnavailableError` (no answer inside the budget); the pipeline maps
    both to `partially_processed`.
    """
    raw = await llm_router.invoke_llm(
        TASK_TYPE,
        [
            {"role": "system", "content": registry.load("project_evidence_system").text},
            {"role": "user", "content": evidence_pack},
        ],
        response_format_json=True,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectReasoningError(
            "The interpretation was not valid JSON."
        ) from exc
    return validate_interpretation(payload)

"""AI job-description generation (FR-3.3 Path A).

Staff supply a short brief (title, requirements, skills, experience, company
context); this service asks an LLM — routed through `llm_router` with the
provider/key fallback chain (claude.md rule 9) — to expand it into a full,
professionally structured JD in the fixed `JDIn`/`JobJD` shape used by
`schemas/jobs.py` (reporting_to, reportees, role, responsibilities,
accountabilities, education, skills[list], experience_years).

Degrades, never crashes (claude.md rule 9): the LLM output is parsed robustly
(prose/code-fence tolerant) with one corrective retry; if the whole provider
chain is unavailable, or every attempt is unusable, a deterministic template JD
is built from the brief and clearly marked so staff know to review it.

Pure service: takes a dict, returns a dict. No DB, no HTTP server.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services import llm_router

logger = logging.getLogger(__name__)

_ROLE_HINT = "extraction"  # content generation is long-context, not latency-sensitive

#: Marker prepended to template-built fields so HR knows the AI was unavailable.
TEMPLATE_NOTICE = "[Draft generated from the brief — AI unavailable, please review.]"

_JD_KEYS = (
    "reporting_to",
    "reportees",
    "role",
    "responsibilities",
    "accountabilities",
    "education",
    "skills",
    "experience_years",
)

_SYSTEM_PROMPT = (
    "You are an expert recruitment copywriter. Expand the staff brief into a "
    "professional, well-structured job description. Cover the company culture "
    "and context, role expectations, responsibilities, accountabilities, "
    "required skills, education/eligibility, and experience.\n"
    "Respond with JSON ONLY, exactly this shape:\n"
    '{"reporting_to": "<who this role reports to, or null>", '
    '"reportees": <integer count of direct reports, or null>, '
    '"role": "<2-4 sentence role summary incl. company culture & expectations>", '
    '"responsibilities": ["<responsibility>", ...], '
    '"accountabilities": ["<accountability / outcome owned>", ...], '
    '"education": "<education & eligibility requirements>", '
    '"skills": ["<skill>", ...], '
    '"experience_years": <integer years of experience required, or null>}\n'
    "Use null for anything the brief does not support. No prose outside the JSON."
)


# ── Robust parsing / coercion ────────────────────────────────────────────────


def _loads_lenient(raw: str) -> Any:
    """Parse JSON that may be wrapped in prose or ```json fences."""
    if not raw:
        raise ValueError("empty response")
    text = raw.strip()
    # Strip a leading/trailing code fence if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the first balanced-looking {...} object.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found in response")


def _as_str_list(value: Any) -> list[str]:
    """Coerce a value into a clean list[str] (skills / responsibilities)."""
    if value is None:
        return []
    if isinstance(value, str):
        # Split prose on newlines / bullets / semicolons so the LLM returning a
        # blob instead of an array still yields a usable list.
        parts = re.split(r"[\n;]+|(?:^|\s)[-*•]\s+", value)
        return [p.strip(" -*•\t") for p in parts if p and p.strip(" -*•\t")]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                s = item.strip()
            elif isinstance(item, dict):
                # e.g. {"skill": "Python"} or {"text": "..."} — take a value.
                s = next((str(v).strip() for v in item.values() if v), "")
            else:
                s = str(item).strip()
            if s:
                out.append(s)
        return out
    return [str(value).strip()] if str(value).strip() else []


def _as_skill_list(value: Any) -> list[str]:
    """Like _as_str_list but also splits comma-separated skill blobs
    ("Python, FastAPI, Postgres") — commas are token separators for skills,
    unlike responsibility sentences where a comma is mid-clause."""
    if isinstance(value, str):
        parts = re.split(r"[\n;,]+|(?:^|\s)[-*•]\s+", value)
        return [p.strip(" -*•\t") for p in parts if p and p.strip(" -*•\t")]
    return _as_str_list(value)


def _as_opt_int(value: Any) -> int | None:
    """Coerce experience_years / reportees to a non-negative int or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group())
    return None


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_jd(data: Any) -> dict | None:
    """Coerce an LLM result into the JDIn shape. Returns None if unusable
    (not a dict, or no usable role/responsibilities/skills signal)."""
    if not isinstance(data, dict):
        return None
    jd: dict[str, Any] = {
        "reporting_to": _as_opt_str(data.get("reporting_to")),
        "reportees": _as_opt_int(data.get("reportees")),
        "role": _as_opt_str(data.get("role")),
        "responsibilities": _as_str_list(data.get("responsibilities")),
        "accountabilities": _as_str_list(data.get("accountabilities")),
        "education": _as_opt_str(data.get("education")),
        "skills": _as_skill_list(data.get("skills")),
        "experience_years": _as_opt_int(data.get("experience_years")),
    }
    # Require at least some substantive content; otherwise treat as unusable so
    # the caller falls back to the deterministic template.
    if not (jd["role"] or jd["responsibilities"] or jd["skills"]):
        return None
    return jd


# ── Deterministic fallback ───────────────────────────────────────────────────


def _template_jd(brief: dict) -> dict:
    """Build a valid JD directly from the brief when the LLM is unavailable.

    Deterministic and clearly marked so HR knows to review it — never raises,
    always returns the full JDIn shape (claude.md rule 9: degrade, don't crash).
    """
    title = str(brief.get("title") or "").strip() or "the role"
    company = str(brief.get("company_context") or "").strip()
    level = str(brief.get("level") or "").strip()
    department = str(brief.get("department") or "").strip()

    role_bits = [f"{TEMPLATE_NOTICE} We are hiring for {title}."]
    if level:
        role_bits.append(f"Level: {level}.")
    if department:
        role_bits.append(f"Department: {department}.")
    if company:
        role_bits.append(company)
    role = " ".join(role_bits)

    requirements = _as_str_list(brief.get("requirements"))
    skills = _as_skill_list(brief.get("skills"))
    # Responsibilities: derive from requirements if given, else a generic line.
    responsibilities = requirements or [
        f"Fulfil the core duties of {title} as defined by the hiring team."
    ]

    return {
        "reporting_to": _as_opt_str(brief.get("reporting_to")),
        "reportees": _as_opt_int(brief.get("reportees")),
        "role": role,
        "responsibilities": responsibilities,
        "accountabilities": [
            f"Own the outcomes and deliverables associated with {title}.",
        ],
        "education": _as_opt_str(brief.get("education"))
        or "As appropriate for the role; see requirements.",
        "skills": skills,
        "experience_years": _as_opt_int(brief.get("experience"))
        if _as_opt_int(brief.get("experience")) is not None
        else _as_opt_int(brief.get("experience_years")),
    }


def _brief_user_message(brief: dict) -> str:
    """Compact JSON view of the brief for the prompt."""
    payload = {
        k: brief.get(k)
        for k in (
            "title",
            "requirements",
            "skills",
            "experience",
            "company_context",
            "department",
            "level",
        )
        if brief.get(k) is not None
    }
    return json.dumps(payload, default=str)


# ── Public API ───────────────────────────────────────────────────────────────


async def generate_job_description(brief: dict) -> dict:
    """Generate a structured job description from a staff brief.

    `brief` keys (all optional except title in practice): title, requirements,
    skills (list), experience, company_context, department, level.

    Returns a dict in the JDIn/JobJD shape: reporting_to, reportees, role,
    responsibilities (list[str]), accountabilities (list[str]), education,
    skills (list[str]), experience_years (int|None).

    Never raises on LLM/content problems — if the provider chain is unavailable
    or every attempt is unusable, a deterministic template JD built from the
    brief is returned (clearly marked). Only unexpected programmer errors
    propagate.
    """
    brief = brief or {}
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _brief_user_message(brief)},
    ]

    raw: str | None = None
    try:
        raw = await llm_router.chat_completion(
            _ROLE_HINT, messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning("jd_generation.llm_unavailable — deterministic template JD")
        return _template_jd(brief)
    except Exception as exc:  # noqa: BLE001 — never crash the caller on the LLM
        logger.warning("jd_generation.llm_error error=%s", type(exc).__name__)
        return _template_jd(brief)

    jd = _try_parse(raw)
    if jd is not None:
        return jd

    # One corrective retry: the model returned prose or a wrong shape.
    corrective = (
        "Your previous response was not valid JSON in the required shape. "
        "Re-emit ONLY a JSON object with exactly these keys: reporting_to, "
        "reportees, role, responsibilities (array of strings), accountabilities "
        "(array of strings), education, skills (array of strings), "
        "experience_years (integer or null). No prose, no markdown."
    )
    retry_messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": corrective},
    ]
    try:
        raw_retry = await llm_router.chat_completion(
            _ROLE_HINT, retry_messages, response_format_json=True
        )
    except llm_router.LLMUnavailableError:
        logger.warning("jd_generation.llm_unavailable_on_retry — deterministic template JD")
        return _template_jd(brief)
    except Exception as exc:  # noqa: BLE001
        logger.warning("jd_generation.llm_retry_error error=%s", type(exc).__name__)
        return _template_jd(brief)

    jd = _try_parse(raw_retry)
    if jd is not None:
        return jd

    logger.warning("jd_generation.unparseable_after_retry — deterministic template JD")
    return _template_jd(brief)


def _try_parse(raw: str) -> dict | None:
    try:
        return _coerce_jd(_loads_lenient(raw))
    except (json.JSONDecodeError, ValueError):
        return None

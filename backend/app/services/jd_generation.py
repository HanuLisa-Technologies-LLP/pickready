"""AI job-description generation (FR-3.3 Path A, reworked 2026-07-28).

THE UNIFIED JD DOCUMENT
-----------------------
The recruiter no longer fills one text box per JD section. They give a short
brief (title, skills, experience band, grade, who the role reports to) and this
service returns ONE formatted Markdown document, `jd_markdown`, with seven
fixed `##` sections: Description, Role, Responsibilities, Accountabilities,
Education, Skills, Experience. That document is the canonical candidate-facing
text; the recruiter edits it in a single editor and then publishes.

The per-section columns in `jd_json` are still populated, by PARSING the
generated Markdown back apart (`parse_jd_markdown`). Nothing downstream that
already reads `jd_json.skills` or `jd_json.responsibilities` breaks, and the
two layers cannot drift because one is derived from the other.

`company_context` and `reportees` were removed from the brief entirely
(client decision, 2026-07-28): the company narrative lives in the three
snapshotted company sections (see api/jobs), and a direct-report count was
never used by anything.

Degrades, never crashes (claude.md rule 9): the LLM output is used as-is when
it is plausible Markdown, retried once when it is not, and finally replaced by
a deterministic template document built from the brief and clearly marked.

NO EM DASHES. The prompt forbids them and `_strip_em_dashes` enforces it on the
way out, because this text is displayed verbatim to candidates.

Pure service: takes a dict, returns a dict. No DB, no HTTP server.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import prompts
from app.services import llm_router
from app.prompts import registry

logger = logging.getLogger(__name__)

# Task-type routing: JD generation is long-form structured writing, so it runs
# on Sonnet 5 (config/llm_providers.MODEL_FOR_TASK). It used to ride the generic
# "extraction" chain; the dedicated task type is a policy change in that table,
# never a change in call shape here.
_ROLE_HINT = "jd_generation"

#: Marker prepended to template-built fields so HR knows the AI was unavailable.
#: No em dash: this string is shown to a recruiter and can reach a published JD
#: if nobody rewrites the draft, and `strip_em_dashes` would rewrite it anyway.
TEMPLATE_NOTICE = "[Draft generated from the brief. AI unavailable, please review.]"

#: The JD section keys carried on `jd_json`. `reportees` was removed on
#: 2026-07-28 and is deliberately absent: nothing read it, and the Create Job
#: form no longer collects it.
_JD_KEYS = (
    "description",
    "reporting_to",
    "role",
    "responsibilities",
    "accountabilities",
    "education",
    "skills",
    "experience_years",
)

#: The seven `##` headings of the unified document, in their fixed order. This
#: tuple is the single source of truth for both the generator's expectations
#: and the parser that splits the document back into `jd_json`.
JD_SECTIONS: tuple[str, ...] = (
    "Description",
    "Role",
    "Responsibilities",
    "Accountabilities",
    "Education",
    "Skills",
    "Experience",
)

#: Heading -> `jd_json` key. Experience is handled separately (it is parsed to
#: a number, not stored as prose).
_HEADING_TO_KEY: dict[str, str] = {
    "description": "description",
    "role": "role",
    "responsibilities": "responsibilities",
    "accountabilities": "accountabilities",
    "education": "education",
    "skills": "skills",
}

#: Sections rendered and parsed as bullet lists.
_LIST_SECTIONS = frozenset({"responsibilities", "accountabilities", "skills"})

#: Text in `app/prompts/jd_generation_system.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_SYSTEM_PROMPT = registry.render("jd_generation_system")


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
                # e.g. {"skill": "Python"} or {"text": "..."}  -  take a value.
                s = next((str(v).strip() for v in item.values() if v), "")
            else:
                s = str(item).strip()
            if s:
                out.append(s)
        return out
    return [str(value).strip()] if str(value).strip() else []


def _as_skill_list(value: Any) -> list[str]:
    """Like _as_str_list but also splits comma-separated skill blobs
    ("Python, FastAPI, Postgres")  -  commas are token separators for skills,
    unlike responsibility sentences where a comma is mid-clause."""
    if isinstance(value, str):
        parts = re.split(r"[\n;,]+|(?:^|\s)[-*•]\s+", value)
        return [p.strip(" -*•\t") for p in parts if p and p.strip(" -*•\t")]
    return _as_str_list(value)


def _as_opt_int(value: Any) -> int | None:
    """Coerce experience_years to a non-negative int or None."""
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
        "description": _as_opt_str(data.get("description") or data.get("role")),
        "reporting_to": _as_opt_str(data.get("reporting_to")),
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

    Deterministic and clearly marked so HR knows to review it  -  never raises,
    always returns the full JDIn shape (claude.md rule 9: degrade, don't crash).
    """
    title = str(brief.get("title") or "").strip() or "the role"
    # `company_context` was removed from the brief on 2026-07-28; the company
    # narrative lives in the three snapshotted company sections instead. Read
    # defensively so a legacy caller still passing one is not punished.
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
        "description": (
            f"{title} is an opportunity to join {company or 'the hiring team'} "
            f"and take ownership of meaningful role outcomes. "
            + " ".join(responsibilities)
        ),
        "reporting_to": _as_opt_str(brief.get("reporting_to")),
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

    Never raises on LLM/content problems  -  if the provider chain is unavailable
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
        logger.warning("jd_generation.llm_unavailable  -  deterministic template JD")
        return _template_jd(brief)
    except Exception as exc:  # noqa: BLE001  -  never crash the caller on the LLM
        logger.warning("jd_generation.llm_error error=%s", type(exc).__name__)
        return _template_jd(brief)

    jd = _try_parse(raw)
    if jd is not None:
        return jd

    # One corrective retry: the model returned prose or a wrong shape.
    corrective = (
        "Your previous response was not valid JSON in the required shape. "
        "Re-emit ONLY a JSON object with exactly these keys: description, reporting_to, "
        "role, responsibilities (array of strings), accountabilities "
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
        logger.warning("jd_generation.llm_unavailable_on_retry  -  deterministic template JD")
        return _template_jd(brief)
    except Exception as exc:  # noqa: BLE001
        logger.warning("jd_generation.llm_retry_error error=%s", type(exc).__name__)
        return _template_jd(brief)

    jd = _try_parse(raw_retry)
    if jd is not None:
        return jd

    logger.warning("jd_generation.unparseable_after_retry  -  deterministic template JD")
    return _template_jd(brief)


def _try_parse(raw: str) -> dict | None:
    try:
        return _coerce_jd(_loads_lenient(raw))
    except (json.JSONDecodeError, ValueError):
        return None


# ── The unified JD document (2026-07-28) ─────────────────────────────────────


#: Em dash, en dash and horizontal bar. Banned from every candidate-facing
#: string on this platform, so the generated document is scrubbed on the way
#: out rather than trusted to have obeyed the prompt.
# The dash characters this module REMOVES, so they are data, not prose. Built
# from chr() rather than written as literals: a repo-wide sweep for U+2014 would
# otherwise rewrite the very character class that exists to strip it, which is
# exactly what happened once already.
_DASHES = re.compile("[" + chr(8212) + chr(8211) + chr(8213) + "]")


def strip_em_dashes(text: str) -> str:
    """Replace every em/en dash with a comma plus space, then tidy spacing.

    A dash in running prose almost always separates two clauses, so a comma is
    the safe substitution. Doubling of spaces or of commas is collapsed so the
    result never reads as a typo.
    """
    if not text:
        return text
    cleaned = _DASHES.sub(",", text)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",{2,}", ",", cleaned)
    cleaned = re.sub(r",(?=\S)", ", ", cleaned)
    return cleaned


def experience_sentence(min_years: Any, max_years: Any) -> str:
    """The Experience paragraph, written from the two year inputs.

    Kept as one function so the LLM path and the template path phrase the band
    identically.
    """
    low = _as_opt_int(min_years)
    high = _as_opt_int(max_years)
    if low is None and high is None:
        return "Experience requirements for this role are set by the hiring team."
    if low is not None and high is not None:
        if low == high:
            return f"This role suits someone with around {low} years of relevant experience."
        return (
            f"This role suits someone with {low} to {high} years of relevant "
            "experience."
        )
    known = low if low is not None else high
    return f"This role suits someone with around {known} years of relevant experience."


def _bullets(values: Any) -> str:
    items = _as_str_list(values)
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items)


def render_jd_markdown(jd: dict, *, min_years: Any = None, max_years: Any = None) -> str:
    """Render a `jd_json`-shaped dict into the seven-section Markdown document.

    Used to (a) build the deterministic fallback document, and (b) backfill
    `jd_markdown` for a job created through the older per-section contract, so
    every job has a canonical document even if it predates this release.
    """
    jd = jd or {}
    blocks: list[str] = []
    for heading in JD_SECTIONS:
        key = heading.lower()
        if key == "experience":
            body = experience_sentence(
                min_years if min_years is not None else jd.get("experience_years"),
                max_years,
            )
        elif key in _LIST_SECTIONS:
            body = _bullets(jd.get(key))
        else:
            body = str(jd.get(key) or "").strip()
        blocks.append(f"## {heading}\n\n{body}".rstrip())
    return strip_em_dashes("\n\n".join(blocks).strip() + "\n")


def parse_jd_markdown(markdown: str) -> dict:
    """Split the unified document back into the `jd_json` section shape.

    The document is canonical; this derivation is what keeps `jd_json.skills`
    and friends alive for the matching pipeline, the public apply page and the
    technical question generator. Tolerant by design: an unknown heading is
    ignored, a missing heading yields an empty section, and `#`/`###` are
    accepted alongside `##` because a recruiter editing by hand will use
    whatever their editor produced.
    """
    result: dict[str, Any] = {
        "description": None,
        "reporting_to": None,
        "role": None,
        "responsibilities": [],
        "accountabilities": [],
        "education": None,
        "skills": [],
        "experience_years": None,
    }
    if not markdown or not markdown.strip():
        return result

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", line)
        if heading:
            current = heading.group(1).strip().lower().rstrip(":")
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)

    for heading, key in _HEADING_TO_KEY.items():
        body = "\n".join(sections.get(heading, [])).strip()
        if key in _LIST_SECTIONS:
            result[key] = _as_str_list(body) if key != "skills" else _as_skill_list(body)
        else:
            result[key] = body or None

    experience_body = "\n".join(sections.get("experience", [])).strip()
    result["experience_years"] = _as_opt_int(experience_body)
    return result


def _document_user_message(brief: dict) -> str:
    """Compact JSON view of the brief for the document prompt."""
    payload = {
        key: brief.get(key)
        for key in (
            "title",
            "skills",
            "experience_min_years",
            "experience_max_years",
            "grade",
            "reporting_to",
            "department",
        )
        if brief.get(key) not in (None, "", [])
    }
    return json.dumps(payload, default=str)


def _looks_like_jd_document(text: str | None) -> bool:
    """Is this plausibly the seven-section document rather than prose or JSON?

    Deliberately lenient: it asks for at least three of the seven headings, so
    a model that dropped one heading still produces a usable draft the
    recruiter can edit, instead of being thrown away for a template.
    """
    if not text or not text.strip():
        return False
    lowered = text.lower()
    found = sum(1 for heading in JD_SECTIONS if f"## {heading.lower()}" in lowered)
    return found >= 3


def _template_document(brief: dict) -> str:
    """Deterministic seven-section document, used when the LLM is unavailable.

    Clearly marked so the recruiter knows to rewrite it before publishing
    (claude.md rule 9: degrade, do not crash).
    """
    title = str(brief.get("title") or "").strip() or "this role"
    skills = _as_skill_list(brief.get("skills"))
    reporting_to = _as_opt_str(brief.get("reporting_to"))
    department = _as_opt_str(brief.get("department"))

    role_bits = [f"{title} sits within the hiring team and owns the outcomes below."]
    if department:
        role_bits.append(f"The role sits in {department}.")
    if reporting_to:
        role_bits.append(f"This role reports to the {reporting_to}.")

    jd = {
        "description": (
            f"{TEMPLATE_NOTICE} We are hiring for {title}. "
            "This draft was written from the brief because the AI writer was "
            "not reachable. Please review and rewrite it before publishing."
        ),
        "role": " ".join(role_bits),
        "responsibilities": [
            f"Deliver the core work of {title} as agreed with the hiring team.",
            "Collaborate with colleagues across the function.",
        ],
        "accountabilities": [f"Own the outcomes and deliverables associated with {title}."],
        "education": "As appropriate for the role. See the skills section.",
        "skills": skills or ["To be confirmed by the hiring team."],
    }
    return render_jd_markdown(
        jd,
        min_years=brief.get("experience_min_years"),
        max_years=brief.get("experience_max_years"),
    )


async def generate_jd_document(brief: dict) -> dict:
    """Generate the unified JD document from a recruiter brief.

    `brief` keys: title, skills (list or string), experience_min_years,
    experience_max_years, grade, reporting_to, department.

    Returns `{"jd_markdown": str, "jd": {...}, "generated_by_ai": bool}` where
    `jd` is the per-section dict parsed straight back out of the document, so
    the two can never disagree.

    Never raises on LLM or content problems: an unreachable provider chain, an
    unusable response after one corrective retry, or empty output all fall back
    to the marked deterministic template.
    """
    brief = brief or {}
    messages = [
        {"role": "system", "content": prompts.load("jd_document")},
        {"role": "user", "content": _document_user_message(brief)},
    ]

    document: str | None = None
    generated_by_ai = True
    try:
        raw = await llm_router.chat_completion(_ROLE_HINT, messages)
        if _looks_like_jd_document(raw):
            document = raw
        else:
            corrective = (
                "That response was not the required document. Re-emit ONLY the "
                "Markdown job description, using the seven '## ' headings in "
                "order: Description, Role, Responsibilities, Accountabilities, "
                "Education, Skills, Experience. No code fences, no JSON, no em "
                "dashes."
            )
            retry = await llm_router.chat_completion(
                _ROLE_HINT,
                messages + [
                    {"role": "assistant", "content": raw or ""},
                    {"role": "user", "content": corrective},
                ],
            )
            if _looks_like_jd_document(retry):
                document = retry
    except llm_router.LLMUnavailableError:
        logger.warning("jd_generation.document_llm_unavailable  -  template document")
    except Exception as exc:  # noqa: BLE001  -  never crash the caller on the LLM
        logger.warning("jd_generation.document_llm_error error=%s", type(exc).__name__)

    if document is None:
        document = _template_document(brief)
        generated_by_ai = False

    # Strip any code fence the model wrapped the document in, then enforce the
    # no-dash rule regardless of what the prompt asked for.
    fence = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", document.strip(), re.DOTALL)
    if fence:
        document = fence.group(1)
    document = strip_em_dashes(document.strip()) + "\n"

    sections = parse_jd_markdown(document)
    # The recruiter's own inputs are authoritative over anything the model
    # inferred: they typed them, the model guessed.
    if _as_opt_str(brief.get("reporting_to")):
        sections["reporting_to"] = _as_opt_str(brief.get("reporting_to"))
    if sections["experience_years"] is None:
        sections["experience_years"] = _as_opt_int(brief.get("experience_min_years"))
    if not sections["skills"]:
        sections["skills"] = _as_skill_list(brief.get("skills"))

    return {
        "jd_markdown": document,
        "jd": sections,
        "generated_by_ai": generated_by_ai,
    }

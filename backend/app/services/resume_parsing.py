"""Resume parsing pipeline (ESD §9, FR-6.2).

Raw PDF/DOCX -> text extraction (pypdf / python-docx) -> LLM extraction chain
(`extraction` role hint: Gemini-first) into a fixed structured schema ->
profiles.parsed_fields_json. Also sets profiles.resume_text and the BGE-M3
embedding used by the semantic matching stage.
"""
from __future__ import annotations

import io
import json
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile
from app.services import llm_router
from app.services.embeddings import embed

_DOWNLOAD_TIMEOUT = 60.0

#: The fixed extraction schema (ESD §9): keep in sync with the prompt below.
PARSED_FIELDS_SCHEMA: dict[str, Any] = {
    "skills": [],
    "total_experience_years": None,
    "education": [],
    "employment_history": [],  # [{company, title, start, end}]
}


class ResumeParsingError(RuntimeError):
    pass


# ── Text extraction ──────────────────────────────────────────────────────────

def extract_text(data: bytes, filename: str) -> str:
    """Extract plain text from a PDF or DOCX resume."""
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return _extract_pdf(data)
    if lowered.endswith(".docx"):
        return _extract_docx(data)
    # ASSUMPTION: unknown extensions are tried as PDF first, then DOCX —
    # Cloudinary URLs may not preserve the original extension.
    try:
        return _extract_pdf(data)
    except Exception:  # noqa: BLE001
        return _extract_docx(data)


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise ResumeParsingError("PDF contained no extractable text (scanned image?)")
    return text


def _extract_docx(data: bytes) -> str:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells if cell.text)
    text = "\n".join(parts).strip()
    if not text:
        raise ResumeParsingError("DOCX contained no extractable text")
    return text


# ── LLM structured extraction ────────────────────────────────────────────────

_EXTRACTION_SYSTEM = (
    "You extract structured data from resumes. Respond with JSON only, exactly "
    "this shape: "
    '{"skills": ["<skill>", ...], '
    '"total_experience_years": <number or null>, '
    '"education": [{"degree": "<str>", "institution": "<str>", "year": <int or null>}], '
    '"employment_history": [{"company": "<str>", "title": "<str>", '
    '"start": "<YYYY-MM or null>", "end": "<YYYY-MM or null (null = current)>"}]}. '
    "Use null for anything not present in the resume. No prose outside the JSON."
)


async def extract_structured_fields(
    resume_text: str, session: AsyncSession | None = None
) -> dict:
    """Run the LLM extraction chain over resume text -> parsed_fields dict."""
    raw = await llm_router.chat_completion(
        "extraction",
        [
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user", "content": resume_text[:24000]},
        ],
        response_format_json=True,
        session=session,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResumeParsingError("LLM returned non-JSON extraction output") from exc
    if not isinstance(parsed, dict):
        raise ResumeParsingError("LLM extraction output was not a JSON object")
    # Normalize to the fixed schema — never store surprise keys.
    return {
        "skills": parsed.get("skills") or [],
        "total_experience_years": parsed.get("total_experience_years"),
        "education": parsed.get("education") or [],
        "employment_history": parsed.get("employment_history") or [],
    }


# ── Full pipeline for one profile ────────────────────────────────────────────

async def parse_resume(session: AsyncSession, profile_id: uuid.UUID | str) -> None:
    """Extract text (if needed), run LLM extraction, store parsed fields,
    resume_text, and the BGE-M3 embedding on the profile."""
    profile = await session.get(Profile, uuid.UUID(str(profile_id)))
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")

    resume_text = profile.resume_text
    if not resume_text:
        if not profile.resume_url:
            raise ResumeParsingError(
                f"Profile {profile_id} has neither resume_text nor resume_url"
            )
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
            resp = await client.get(profile.resume_url)
            resp.raise_for_status()
        resume_text = extract_text(resp.content, profile.resume_url.split("?")[0])

    parsed = await extract_structured_fields(resume_text, session=session)

    profile.resume_text = resume_text
    profile.parsed_fields_json = parsed
    profile.embedding = (await embed([resume_text]))[0]
    await session.commit()

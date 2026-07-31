"""Employer verification-reply parsing (ESD §10, FR-5.3 fallback path).

When an employer replies to the verification email instead of using the
tokenized web form, the inbound-email webhook enqueues this LLM extraction  - 
same structured schema as the form, so both paths write identical
`verification_requests.response_json` shapes.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import llm_router

logger = logging.getLogger(__name__)

#: Canonical response schema  -  identical to the public form fields
#: (POST /verification/form/{token} in API_CONTRACT.md).
VERIFICATION_FIELDS = (
    "designation",
    "doj",                          # date of joining, YYYY-MM-DD when stated
    "doe",                          # date of exit, YYYY-MM-DD when stated
    "last_drawn_ctc",
    "last_drawn_gross",
    "noc_status",
    "exit_formalities_complete",    # boolean when determinable
    "bgv_status",
    "proofs_details",               # educational/address/ID proof commentary
    "prior_experience_details",     # prior experience/compensation commentary
)


class VerificationParsingError(RuntimeError):
    pass


_SYSTEM_PROMPT = (
    "You extract structured employment-verification data from an HR "
    "department's email reply about a former employee. Respond with JSON "
    "only, exactly these keys: "
    '{"designation": <str or null>, '
    '"doj": <"YYYY-MM-DD" or null>, '
    '"doe": <"YYYY-MM-DD" or null>, '
    '"last_drawn_ctc": <str or null>, '
    '"last_drawn_gross": <str or null>, '
    '"noc_status": <str or null>, '
    '"exit_formalities_complete": <true, false, or null>, '
    '"bgv_status": <str or null>, '
    '"proofs_details": <str or null>, '
    '"prior_experience_details": <str or null>}. '
    "Use null for anything the reply does not state. Do not guess or infer "
    "values that are not explicitly present. No prose outside the JSON."
)


async def parse_reply(
    raw_email_text: str, session: AsyncSession | None = None
) -> dict:
    """Extract the verification schema from a raw employer email reply.

    Returns a dict containing exactly VERIFICATION_FIELDS. When the reply is
    unparseable (the LLM returns non-JSON / a non-object  -  e.g. an out-of-band
    prose reply), every field degrades to None and a warning is logged rather
    than raising, so a junk reply never crash-loops the Celery task. Only truly
    empty input raises VerificationParsingError; llm_router.LLMUnavailableError
    (whole provider chain down) propagates so the task's retry policy applies.
    """
    if not raw_email_text or not raw_email_text.strip():
        raise VerificationParsingError("Empty email reply text")

    raw = await llm_router.chat_completion(
        "extraction",
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": raw_email_text[:24000]},
        ],
        response_format_json=True,
        session=session,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("verification_parsing.non_json_output  -  all fields null")
        parsed = {}
    if not isinstance(parsed, dict):
        logger.warning("verification_parsing.non_object_output  -  all fields null")
        parsed = {}

    # Normalize to exactly the canonical schema  -  both submission paths
    # (form and email reply) must produce identical shapes.
    return {field: parsed.get(field) for field in VERIFICATION_FIELDS}

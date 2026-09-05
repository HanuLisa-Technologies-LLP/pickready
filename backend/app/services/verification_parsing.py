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
from app.prompts import registry

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


#: Text in `app/prompts/verification_reply_extraction_system.txt`, loaded through the registry so a
#: wording change is a versioned diff in a prompt file rather than a string
#: literal in a module of code. What is sent is unchanged.
_SYSTEM_PROMPT = registry.render("verification_reply_extraction_system")


async def parse_reply(
    raw_email_text: str, session: AsyncSession | None = None
) -> dict:
    """Extract the verification schema from a raw employer email reply.

    Returns a dict containing exactly VERIFICATION_FIELDS. When the reply is
    unparseable (the LLM returns non-JSON / a non-object  -  e.g. an out-of-band
    prose reply), every field degrades to None and a warning is logged rather
    than raising, so a junk reply never crash-loops the task. Only truly
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

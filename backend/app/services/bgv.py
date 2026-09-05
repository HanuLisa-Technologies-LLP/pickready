"""Background-verification helpers (add-features spec, 2026-09-05,
"Candidate Verification": background verification via departmental HR email).

Three concerns, all candidate-side:

* `domain_match` -- a DETERMINISTIC comparison of the departmental mailbox's
  domain against the employer's name. No model call: this is provenance a
  recruiter reads beside the parsed fields, and provenance that varied with a
  provider's sampling would be worthless. It never blocks anything.
* `is_free_provider_domain` -- refuses personal-mail domains at intake. A
  reply from gmail.com proves a person owns a mailbox, not that a company's
  HR department answered.
* `parse_reply` -- extracts the spec's seven fields from the employer's email
  reply through the LLM router (extraction tier, JSON mode) with a
  deterministic validator. A failed parse RAISES so the caller lands the row
  in `parse_failed` honestly; no template output is ever presented as an
  extraction.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bgv import BGV_FIELDS
from app.prompts import registry
from app.services import llm_router

# ── Free-provider refusal ────────────────────────────────────────────────────
#
# ONE list for one concept: the corporate-sender work (2026-09-05 spec,
# section 2) put the free/personal-provider list in
# `settings.sender_domain_blocklist`, parsed by
# `Settings.sender_blocked_domains()`. This module reads that same setting
# rather than carrying a second copy that would drift, and matches SUBDOMAINS
# of a blocked domain too, exactly as the sender registration does, so
# hr@mail.yahoo.com cannot slip past the check that refuses hr@yahoo.com.

_EMAIL_RE = re.compile(r"^[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")

#: Multi-part public suffixes seen on Indian and common corporate domains.
#: Checked before the generic single-label suffix so "infosys.co.in" yields
#: the registrable label "infosys", not "co".
_TWO_PART_SUFFIXES: frozenset[str] = frozenset(
    {"co.in", "com.in", "net.in", "org.in", "ac.in", "gov.in", "co.uk", "com.au"}
)

#: Legal-suffix and glue tokens dropped from the employer name before
#: comparison (spec: "normalize: lowercase, strip legal suffixes").
_LEGAL_TOKENS: frozenset[str] = frozenset(
    {
        "ltd", "limited", "llp", "inc", "incorporated", "pvt", "private",
        "plc", "corp", "corporation", "co", "company", "the", "and", "of",
        "group", "india",
    }
)


def blocked_email_domains() -> frozenset[str]:
    """The refusal list, read from the one `sender_domain_blocklist` setting
    the corporate-sender registration also enforces (see the note above)."""
    return get_settings().sender_blocked_domains()


def email_domain(email: str) -> str | None:
    """The domain of a syntactically plausible address, lowercased; None when
    the address does not parse."""
    match = _EMAIL_RE.match((email or "").strip())
    return match.group(1).lower() if match else None


def is_free_provider_domain(email: str) -> bool:
    """True when the mailbox's domain is a blocked free/personal provider or
    any subdomain of one."""
    domain = email_domain(email)
    if domain is None:
        return False
    blocked = blocked_email_domains()
    return domain in blocked or any(
        domain.endswith("." + entry) for entry in blocked
    )


def registrable_label(domain: str) -> str | None:
    """The organisation-identifying label of a domain: "tcs" for tcs.com,
    "infosys" for hr.infosys.co.in. None when it cannot be determined."""
    parts = [p for p in domain.lower().split(".") if p]
    if len(parts) < 2:
        return None
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_SUFFIXES:
        return parts[-3]
    return parts[-2]


def _name_tokens(employer_name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (employer_name or "").lower())
    return [w for w in words if w not in _LEGAL_TOKENS]


def domain_match(employer_name: str, departmental_email: str) -> str:
    """matched | mismatched | indeterminate. Deterministic, never a gate.

    "matched" when the email domain's registrable label lines up with the
    employer name: the joined name, any single meaningful token, the name's
    acronym, or a long-enough token contained in the label (which is how
    "Infosys BPM" matches infosysbpm.com and "Tata Consultancy Services"
    matches tcs.com). "indeterminate" when either side is too degenerate to
    compare, which is an honest answer, not a soft pass.
    """
    domain = email_domain(departmental_email)
    if domain is None:
        return "indeterminate"
    label = registrable_label(domain)
    tokens = _name_tokens(employer_name)
    if label is None or len(label) < 2 or not tokens:
        return "indeterminate"

    joined = "".join(tokens)
    acronym = "".join(t[0] for t in tokens)
    if label == joined:
        return "matched"
    if any(label == t and len(t) >= 3 for t in tokens):
        return "matched"
    if len(tokens) >= 2 and len(acronym) >= 3 and label == acronym:
        return "matched"
    # Subsidiary-style overlap: a distinctive name token inside the label, or
    # the whole label inside the joined name. Four characters is the floor so
    # "co" or "hr" can never manufacture a match.
    if any(len(t) >= 4 and t in label for t in tokens):
        return "matched"
    if len(label) >= 4 and label in joined:
        return "matched"
    return "mismatched"


# ── Reply parsing ────────────────────────────────────────────────────────────


class BGVParseError(RuntimeError):
    """The reply could not be parsed into the seven fields. The caller lands
    the inquiry in `parse_failed`; the raw reply is retained either way."""


#: Text in `app/prompts/bgv_reply_extraction_system.txt`, loaded through the
#: registry so a wording change is a versioned prompt diff.
_SYSTEM_PROMPT = registry.render("bgv_reply_extraction_system")

_MAX_FIELD_CHARS = 500


def validate_parsed_fields(parsed: object) -> dict:
    """Deterministic validator: exactly the BGV_FIELDS keys, each None or a
    bounded string (`exit_formalities` may also be a boolean). Raises
    BGVParseError on a non-object payload or when nothing was extracted at
    all -- seven nulls is a failed extraction, not a result."""
    if not isinstance(parsed, dict):
        raise BGVParseError("Extraction returned a non-object payload")
    out: dict = {}
    for field in BGV_FIELDS:
        value = parsed.get(field)
        if value is None:
            out[field] = None
        elif field == "exit_formalities" and isinstance(value, bool):
            out[field] = value
        elif isinstance(value, (str, int, float, bool)):
            text = str(value).strip()
            out[field] = text[:_MAX_FIELD_CHARS] if text else None
        else:
            # A list or nested object is not a field value; drop it rather
            # than serialise structure into a display string.
            out[field] = None
    if all(v is None for v in out.values()):
        raise BGVParseError("The reply stated none of the requested fields")
    return out


async def parse_reply(
    raw_email_text: str, session: AsyncSession | None = None
) -> dict:
    """Extract the seven BGV fields from an employer's email reply.

    Raises BGVParseError on empty input, non-JSON output, a non-object, or an
    all-null extraction: the task marks the row `parse_failed` and keeps the
    raw reply. `llm_router.LLMUnavailableError` propagates so the task's retry
    policy applies to a provider outage rather than mislabelling it a parse
    failure.
    """
    if not raw_email_text or not raw_email_text.strip():
        raise BGVParseError("Empty reply text")

    raw = await llm_router.chat_completion(
        "bgv_reply_extraction",
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": raw_email_text[:24000]},
        ],
        response_format_json=True,
        session=session,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BGVParseError("Extraction returned non-JSON output") from exc
    return validate_parsed_fields(parsed)

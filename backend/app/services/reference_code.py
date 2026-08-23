"""The company | job | candidate reference code shown under every name.

WHAT IT IS FOR
--------------
One short, stable, human-readable handle for "this candidate, on this job, at
this company". A recruiter reads a name in a list, a name in an email and a
name at the top of a report, and nothing in the product previously told them
those were the same application -- names repeat, and the underlying identifiers
are UUIDs nobody can hold in their head or read down a phone line.

WHY THREE SEGMENTS AND NOT ONE HASH
-----------------------------------
The code is written `COMPANY-JOB-CANDIDATE`, one segment derived from each
identifier, rather than one opaque digest over all three. That is the whole
usefulness of it: two rows on the same job share their first two segments and
differ only in the third, so a person can see at a glance that two codes belong
to the same posting. A single hash over the triple is unique too, and tells the
reader nothing.

WHY IT IS KEYED, AND WHY IT IS NOT REVERSIBLE
---------------------------------------------
Each segment is an HMAC of the identifier under the application's signing
secret, not the identifier itself and not a bare hash of it. Two properties
follow, and both matter:

  * The code carries no data. A candidate who is shown their own code, or an
    interviewer who is forwarded one, learns nothing about the database from
    it. A bare truncated SHA of a UUID would be reversible by anyone who could
    guess the input space; a raw id embedded in the code would leak the id.
  * The company segment is the same on every row for that company, so a code
    from one tenant can never be mistaken for a code from another. It is a
    LABEL, though, never a permission: nothing anywhere authorises on this
    value, and RLS remains the tenant boundary.

WHY IT IS DERIVED AND NOT STORED
--------------------------------
It is a pure function of three ids that never change, so a column would only
add a way for the stored value and the computed one to disagree. Nothing writes
it, no migration is needed, and a report written a year ago renders the same
code today.

ALPHABET
--------
Crockford base32 without I, L, O and U: the four characters that get misread or
mistyped when a code is read aloud or copied off a screen, which is exactly what
this is for. Uppercase, in fixed-width groups.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

#: Crockford base32. No I/L/O (confusable with 1/1/0) and no U (so a code
#: cannot spell an unfortunate word by accident).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Characters per segment. Four gives 32^4 = ~1.05 million values per segment.
#: Collisions do not matter for the company and job segments -- they are read
#: alongside a page that already names the company and the job -- and within one
#: job a collision would need two candidates whose HMACs agree in 20 bits, which
#: `distinct_codes` is the guard for.
SEGMENT_LENGTH = 4

#: What separates the segments when rendered. The spec writes the concept as
#: "company|job|candidate"; a hyphen is what survives being copied into a
#: spreadsheet cell, a URL, or a subject line.
SEPARATOR = "-"

_PREFIX_COMPANY = b"readypick:company:"
_PREFIX_JOB = b"readypick:job:"
_PREFIX_CANDIDATE = b"readypick:candidate:"


def _secret() -> bytes:
    from app.core.config import get_settings

    return str(get_settings().jwt_secret).encode("utf-8")


def _segment(prefix: bytes, identifier: Any) -> str:
    """One HMAC'd, base32-encoded segment.

    The prefix is DOMAIN SEPARATION and is not decorative: without it a job id
    and a candidate id that happened to be the same UUID would produce the same
    segment, and more importantly a value computed for one position could be
    replayed into another.
    """
    if identifier is None:
        # A missing id is rendered rather than raised. The code is a display
        # aid, and a half-built row (a candidate not yet linked to a job) must
        # still render a name.
        return "0" * SEGMENT_LENGTH
    digest = hmac.new(
        _secret(), prefix + str(identifier).encode("utf-8"), hashlib.sha256
    ).digest()
    # Read 5 bits at a time out of the leading bytes, which is a base32 digit.
    value = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(SEGMENT_LENGTH):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def reference_code(
    tenant_id: Any, job_id: Any, candidate_id: Any
) -> str:
    """`COMPANY-JOB-CANDIDATE`, e.g. `K7QP-2M4X-9TB1`.

    Stable for the life of the three rows and identical in every surface that
    renders it.
    """
    return SEPARATOR.join(
        (
            _segment(_PREFIX_COMPANY, tenant_id),
            _segment(_PREFIX_JOB, job_id),
            _segment(_PREFIX_CANDIDATE, candidate_id),
        )
    )


def company_segment(tenant_id: Any) -> str:
    """Just the company segment, for a surface that has no job or candidate."""
    return _segment(_PREFIX_COMPANY, tenant_id)


def job_reference(tenant_id: Any, job_id: Any) -> str:
    """`COMPANY-JOB`, for the job page header."""
    return SEPARATOR.join(
        (_segment(_PREFIX_COMPANY, tenant_id), _segment(_PREFIX_JOB, job_id))
    )


def is_wellformed(code: str) -> bool:
    """Shape check only. There is no `decode`: the code is one-way by design."""
    parts = str(code or "").split(SEPARATOR)
    if len(parts) != 3:
        return False
    return all(
        len(part) == SEGMENT_LENGTH and all(ch in _ALPHABET for ch in part)
        for part in parts
    )

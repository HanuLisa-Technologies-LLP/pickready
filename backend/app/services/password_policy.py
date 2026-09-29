"""The one password rule, checked on the server wherever this product sets one.

The screens show the same four rules (`frontend/components/password-rules.tsx`:
eight or more characters, an uppercase letter, a lowercase letter, a number).
Until 2026-09-29 only the browser checked them, because only the browser set a
password. The invitation setup, the password change and the password reset now
set it on the SERVER, so the rule is enforced here, and a request that skips
the screen is held to it too. The password itself is never logged or stored.
"""
from __future__ import annotations

import re

from fastapi import HTTPException, status

__all__ = ["MIN_LENGTH", "PASSWORD_RULE_DETAIL", "problem", "require_valid"]

MIN_LENGTH = 8
MAX_LENGTH = 128

PASSWORD_RULE_DETAIL = (
    "Use at least 8 characters with an uppercase letter, a lowercase letter "
    "and a number."
)


def problem(password: str) -> str | None:
    """The rule sentence when `password` breaks it, else None."""
    value = password or ""
    if (
        len(value) < MIN_LENGTH
        or len(value) > MAX_LENGTH
        or not re.search(r"[a-z]", value)
        or not re.search(r"[A-Z]", value)
        or not re.search(r"\d", value)
    ):
        return PASSWORD_RULE_DETAIL
    return None


def require_valid(password: str) -> None:
    """422 with the rule sentence when the password breaks it."""
    detail = problem(password)
    if detail:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

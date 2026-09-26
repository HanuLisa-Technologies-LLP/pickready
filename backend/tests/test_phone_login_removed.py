"""Phone sign-in is removed, and stays removed.

Owner ruling, Vivekium release: no screen ever offered phone sign-in and
Firebase owns identity. What was left was worse than nothing: `api/auth`
matched users on `users.phone` through a set of "safe equivalent forms",
created phone-only candidates, and the workspace chooser matched a phone too,
so two people sharing a number in imported data would each have been offered
the other's workspace.

What survives, deliberately:

* `firebase_auth.assert_provider_allowed` REFUSES the "phone" provider, by its
  absence from `ALLOWED_PROVIDERS`. That refusal is the enforcement.
* `users.phone` and `users.phone_verified_at` stay as CONTACT data (S4: no
  drop of columns that may hold customer rows). They identify nobody.

The sweep is the shared whitespace-normalised one (`tests/removal_sweep.py`),
so a pattern wrapped across a line still matches.
"""
from __future__ import annotations

import dataclasses
import inspect
import pathlib
import re

from app.services import firebase_auth, login_context
from tests.removal_sweep import sweep

#: One alternative per piece of the retired phone login: the alias helper, the
#: Firebase phone claim, a read of a phone off the identity, a branch on the
#: phone provider, the phone-reuse refusal, and the chooser's phone match.
GONE = re.compile(
    r"_phone_aliases"
    r"|phone_number"
    r"|identity\.phone"
    r"|provider ?== ?[\"']phone[\"']"
    r"|phone number is linked to multiple accounts"
    r"|User\.phone ?== ?identifier"
)

#: This file states the patterns; nothing else is exempt.
THIS_FILE = pathlib.Path(__file__)


def test_no_phone_login_code_remains() -> None:
    hits = sweep(GONE, exempt=(THIS_FILE,))
    assert not hits, "phone sign-in code is back:\n" + "\n".join(hits)


def test_the_identity_has_no_phone_field() -> None:
    fields = {field.name for field in dataclasses.fields(firebase_auth.FirebaseIdentity)}
    assert "phone" not in fields


def test_the_phone_provider_is_still_refused() -> None:
    """Removing the code is not the same as refusing the provider; both hold."""
    assert "phone" not in firebase_auth.ALLOWED_PROVIDERS


def test_the_workspace_lookup_reads_email_only() -> None:
    assert "User.phone" not in inspect.getsource(login_context.find_users)

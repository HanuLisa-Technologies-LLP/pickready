"""The multi-vendor LLM roster is gone, and this is what keeps it gone.

WHAT WENT
---------
spec-doc5 Part B (2026-08-28) consolidated every model call onto one vendor
and stopped reading the encrypted key roster; the table and its mapping were
kept unread so a rollback could find the rows. The Vivekium release's Phase 7
Wave B (WP-B2) finished it: the pilot probe found the table empty, migration
0128 dropped it behind an emptiness guard, and the `LLMProviderKey` mapping
and the `LLMProvider` / `LLMRoleHint` enums (Groq, Gemini, OpenRouter, and the
"rerank" and "extraction" routing hints) were deleted with it.

`MODEL_FOR_TASK` stays a closed mapping onto two ids; that is
`test_llm_task_routing.py`'s job, not this file's. This file only keeps the
retired roster's names out of the tree.

THE PENDING LIST
----------------
The one setting that decrypted those rows, and its `.env.example` entry, were
deleted by WP-B3 in the same wave. The secret
CONTAINER stays in Terraform, granted to no service (CONTRACT v2), and is not
named by the patterns below. Same ratchet as `test_login_otp_removed`: a
mention outside the list fails, and a listed file that no longer mentions the
names fails too.
"""
from __future__ import annotations

import pathlib
import re

from tests.removal_sweep import sweep

THIS_FILE = pathlib.Path(__file__).resolve()
#: The sibling removal sweep of the same change asserts its own names are
#: absent by naming them, the same reason this file is exempt from itself.
SIBLING = THIS_FILE.parent / "test_legacy_scrap_removed.py"

GONE = re.compile(
    r"\bLLMProviderKey\b|\bLLMProvider\b|\bLLMRoleHint\b|llm_provider_keys"
)

#: file -> the package that removes the mention. Empty since the stage 3
#: integration: WP-B3 deleted the setting and its `.env.example` entry, and the
#: held key's comments name the roster without naming its table.
PENDING: dict[str, str] = {}


def _files(hits: list[str]) -> set[str]:
    return {hit.split(":", 1)[0].replace("\\", "/") for hit in hits}


def test_nothing_names_the_retired_roster() -> None:
    hits = sweep(GONE, exempt=(THIS_FILE, SIBLING))
    spread = sorted(_files(hits) - set(PENDING))
    assert not spread, (
        "A retired multi-vendor name appeared outside the pending list. Remove "
        f"it rather than listing the file: {spread}"
    )


def test_the_pending_list_only_shrinks() -> None:
    stale = sorted(set(PENDING) - _files(sweep(GONE, exempt=(THIS_FILE, SIBLING))))
    assert not stale, stale


def test_the_mapping_and_the_enums_are_gone() -> None:
    import app.models as models
    from app.models import enums, tenant

    assert not hasattr(tenant, "LLMProviderKey")
    assert not hasattr(enums, "LLMProvider")
    assert not hasattr(enums, "LLMRoleHint")
    for name in ("LLMProviderKey", "LLMProvider", "LLMRoleHint"):
        assert name not in models.__all__, name

"""Prompt versioning: which text wrote this output, still answerable later.

THE PROBLEM
-----------
`app/prompts/registry.py` loads a prompt by name. Improve the prompt, and every
report written by the old one becomes unexplainable: the text that produced it
no longer exists anywhere, so "why did it say that" has no answer and a
regression cannot be separated from a provider sampling differently.

WHAT THIS ADDS, AND WHAT IT DOES NOT
-------------------------------------
A content fingerprint per prompt name, resolved at load time and cheap enough to
stamp onto a trace. It does NOT move the prompt files or change how they are
loaded -- fourteen prompts and a loader that works are not worth churning for a
hash. A/B selection is a thin function over the same registry so that when a
prompt IS varied deliberately, which arm ran is recorded rather than inferred.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

from app.prompts import registry


@lru_cache(maxsize=64)
def fingerprint(name: str) -> str:
    """A short, stable hash of a prompt's text.

    Cached: prompts are read from disk into an image that does not change under
    a running process, so re-hashing per call would be pure waste.
    """
    try:
        text = registry.load(name)
    except Exception:  # noqa: BLE001 -- a missing prompt is the loader's error to raise
        return "unknown"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def variant_for(name: str, key: str, arms: tuple[str, ...]) -> str:
    """Pick an A/B arm DETERMINISTICALLY from a stable key.

    Deterministic on the key -- a job id, a link id -- rather than random, so the
    same candidate never gets arm A on one turn and arm B on the next. An
    interview that switches prompt mid-conversation is not an experiment, it is
    two half-experiments and an inconsistent candidate experience.
    """
    if not arms:
        return name
    digest = hashlib.sha256(f"{name}:{key}".encode("utf-8")).digest()
    return arms[digest[0] % len(arms)]

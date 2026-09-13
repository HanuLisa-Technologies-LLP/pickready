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


#: The name every other memory layer uses when it records that a prompt is what
#: produced something. Written once here so a revocation naming a prompt
#: version (`revoke_learnings_from_source`) and the write that recorded it
#: cannot spell the source differently.
SOURCE = "prompt"


def source_version(name: str) -> str:
    """The `source_version` a caller records for output written by this prompt.

    This is the provenance half of W3.5 that this layer supplies: a learning or
    a cached fact derived from a prompt records WHICH TEXT wrote it, so when a
    prompt version turns out to have taught the wrong lesson, everything it
    produced is identifiable by exactly this string.
    """
    return f"{name}:{fingerprint(name)}"


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



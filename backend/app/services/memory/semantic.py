"""Cached facts about one entity, and the TTLs that keep them honest.

Every TTL here is chosen against how the underlying row actually changes:

  candidate   short. A profile is rewritten by an async parse that can land at
              any moment after upload.
  job         medium. Edited by a recruiter during setup, rarely afterwards.
  framework   long. Frozen once anyone has been assessed against it.

There is no entry for a transcript, and that absence is deliberate: a live
conversation grows between two reads, so an agent scoring a cached one is
scoring the wrong assessment.
"""
from __future__ import annotations

from typing import Any

from app.core import cache

TTL_CANDIDATE = 120
TTL_JOB = 300
TTL_FRAMEWORK = 3600


def key_for(kind: str, entity_id: Any) -> str:
    return cache.key("semantic", kind, str(entity_id))


async def remember(kind: str, entity_id: Any, value: Any, *, ttl: int) -> bool:
    return await cache.set(key_for(kind, entity_id), value, ttl=ttl)


async def recall(kind: str, entity_id: Any) -> Any | None:
    return await cache.get(key_for(kind, entity_id))


async def forget(kind: str, entity_id: Any) -> None:
    """Called by the WRITE path. A cache the writer does not invalidate is a
    cache that serves the value the writer just replaced."""
    await cache.invalidate(key_for(kind, entity_id))

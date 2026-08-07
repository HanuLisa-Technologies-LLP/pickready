"""Small resilient Redis cache for tenant-scoped read-mostly data."""
from __future__ import annotations

import json
from typing import Any

from redis import asyncio as aioredis

from app.core.config import get_settings


def _client():
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


async def get_json(key: str) -> Any | None:
    client = _client()
    try:
        value = await client.get(key)
        return json.loads(value) if value else None
    except Exception:
        return None
    finally:
        await client.aclose()


async def set_json(key: str, value: Any, *, ttl: int = 120) -> None:
    client = _client()
    try:
        await client.set(key, json.dumps(value), ex=ttl)
    except Exception:
        pass
    finally:
        await client.aclose()


async def delete(key: str) -> None:
    client = _client()
    try:
        await client.delete(key)
    except Exception:
        pass
    finally:
        await client.aclose()


async def delete_pattern(pattern: str) -> None:
    client = _client()
    try:
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
    except Exception:
        pass
    finally:
        await client.aclose()

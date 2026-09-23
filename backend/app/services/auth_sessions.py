"""Server-side browser sessions. Redis TTL is the 30-minute idle deadline.

JWT expiry limits a stolen access token; the session record independently
controls whether either cookie can still authenticate. Redis is required here:
an unavailable session store must never turn revocation into a fail-open check.
"""
import secrets
import time
import uuid

from fastapi import HTTPException

from app.core import cache

IDLE_SECONDS = 30 * 60
ROTATION_GRACE_SECONDS = 15  # concurrent tabs may present the prior cookie


def _client():
    client = cache._redis()  # noqa: SLF001 - shared, loop-aware Redis client
    if client is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    return client


def _key(sid: str) -> str:
    return f"auth:session:{sid}"


def _index(user_id: str) -> str:
    return f"auth:user-sessions:{user_id}"


def new_id() -> str:
    return secrets.token_urlsafe(24)


async def create(sid: str, user_id: uuid.UUID | str, refresh_jti: str, refresh_token: str) -> None:
    user = str(user_id)
    client = _client()
    try:
        pipe = client.pipeline(transaction=True)
        pipe.hset(_key(sid), mapping={
            "user": user, "jti": refresh_jti, "token": refresh_token,
            "previous_jti": "", "previous_until": "0",
        })
        pipe.expire(_key(sid), IDLE_SECONDS)
        pipe.sadd(_index(user), sid)
        pipe.expire(_index(user), IDLE_SECONDS)
        await pipe.execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc


_TOUCH = """
local user = redis.call('HGET', KEYS[1], 'user')
if not user or user ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
return 1
"""


async def validate(sid: str, user_id: uuid.UUID | str) -> bool:
    user = str(user_id)
    try:
        return bool(await _client().eval(
            _TOUCH, 2, _key(sid), _index(user), user, IDLE_SECONDS
        ))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc


_ROTATE = """
local user = redis.call('HGET', KEYS[1], 'user')
if not user or user ~= ARGV[1] then return nil end
local current = redis.call('HGET', KEYS[1], 'jti')
local token
if current == ARGV[2] then
  redis.call('HSET', KEYS[1], 'previous_jti', current,
    'previous_until', ARGV[5], 'jti', ARGV[3], 'token', ARGV[4])
  token = ARGV[4]
elseif redis.call('HGET', KEYS[1], 'previous_jti') == ARGV[2]
  and tonumber(redis.call('HGET', KEYS[1], 'previous_until')) >= tonumber(ARGV[6]) then
  token = redis.call('HGET', KEYS[1], 'token')
else
  return nil
end
redis.call('EXPIRE', KEYS[1], ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[7])
return token
"""


async def rotate(
    sid: str, user_id: uuid.UUID | str, old_jti: str,
    new_jti: str, new_token: str,
) -> str | None:
    """Atomically rotate, returning the winner's token to racing tabs."""
    user = str(user_id)
    now = int(time.time())
    try:
        return await _client().eval(
            _ROTATE, 2, _key(sid), _index(user), user, old_jti,
            new_jti, new_token, now + ROTATION_GRACE_SECONDS, now,
            IDLE_SECONDS,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc


async def revoke(sid: str, user_id: uuid.UUID | str) -> None:
    user = str(user_id)
    try:
        client = _client()
        pipe = client.pipeline(transaction=True)
        pipe.delete(_key(sid))
        pipe.srem(_index(user), sid)
        await pipe.execute()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc


async def revoke_all(user_id: uuid.UUID | str) -> None:
    user = str(user_id)
    try:
        client = _client()
        members = await client.smembers(_index(user))
        pipe = client.pipeline(transaction=True)
        for sid in members:
            pipe.delete(_key(sid))
        pipe.delete(_index(user))
        await pipe.execute()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc

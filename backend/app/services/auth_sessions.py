"""Server-side browser sessions. Redis TTL is the 30-minute idle deadline.

JWT expiry limits a stolen access token; the session record independently
controls whether either cookie can still authenticate. Redis is required here:
an unavailable session store must never turn revocation into a fail-open check.

THE IDLE DEADLINE MOVES ONLY FOR A PERSON, NEVER FOR A TIMER
------------------------------------------------------------
Until the Vivekium release every authenticated request pushed the deadline
out, and so did every refresh. A tab left open on a page that polls (a job's
matching progress, the messages list, a timer) therefore kept its session
alive for ever, and "thirty minutes idle" meant "thirty minutes with the tab
closed". Worse, the poll's 401 after the fifteen-minute access expiry was
answered by a refresh that ALSO renewed, so even the access-token bound did not
bound it.

Now the caller says whether the request is activity. `validate(touch=True)` and
`rotate(touch=True)` renew; `touch=False` checks and changes nothing. The
request dependency decides from the `X-User-Activity` header, which the browser
sends only within a few seconds of a real pointer, key or touch event
(`frontend/lib/user-activity.ts`). The flag is REQUIRED on both functions, with
no default, so a new caller has to decide rather than inherit either answer.
`create` always sets the deadline: signing in is activity.
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


async def validate(sid: str, user_id: uuid.UUID | str, *, touch: bool) -> bool:
    """True while the session exists and belongs to `user_id`.

    `touch=True` (a request carrying real user activity) also pushes the idle
    deadline out by `IDLE_SECONDS`; `touch=False` is a pure read. The read is a
    single HGET, which is atomic on its own and needs no script: the owner
    check is the only thing it asks, and it changes nothing.
    """
    user = str(user_id)
    try:
        client = _client()
        if touch:
            return bool(await client.eval(
                _TOUCH, 2, _key(sid), _index(user), user, IDLE_SECONDS
            ))
        return await client.hget(_key(sid), "user") == user
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
if ARGV[8] == '1' then
  redis.call('EXPIRE', KEYS[1], ARGV[7])
  redis.call('EXPIRE', KEYS[2], ARGV[7])
end
return token
"""


async def rotate(
    sid: str, user_id: uuid.UUID | str, old_jti: str,
    new_jti: str, new_token: str, *, touch: bool,
) -> str | None:
    """Atomically rotate, returning the winner's token to racing tabs.

    A refresh is usually a poll's 401 being repaired, not a person doing
    anything, so it renews the idle deadline only when `touch` says the
    refresh was triggered by real activity.
    """
    user = str(user_id)
    now = int(time.time())
    try:
        return await _client().eval(
            _ROTATE, 2, _key(sid), _index(user), user, old_jti,
            new_jti, new_token, now + ROTATION_GRACE_SECONDS, now,
            IDLE_SECONDS, "1" if touch else "0",
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

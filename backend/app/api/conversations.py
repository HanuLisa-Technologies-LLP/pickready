"""Conversations: REST for history and sending, one WebSocket for live updates.

AUTHORISATION IS ASKED OF THE ROW, ON EVERY ENTRY POINT, INCLUDING THE SOCKET
------------------------------------------------------------------------------
Every route and the socket call `conversations.authorize_participant`, which
re-reads the conversation under the caller's own RLS session and compares the
tenant. A conversation id arriving from a browser is a claim, never a fact, and
a conversation belonging to another customer is NOT FOUND rather than forbidden,
which is the rule cross-tenant reads already follow everywhere else here.

THE SOCKET ADDS NO AUTHORITY, AND IT IS READ ONLY BY CONSTRUCTION
------------------------------------------------------------------
It authenticates with the same cookie the REST routes use, checks the same
capability, joins exactly one conversation it has already been authorised for,
and accepts no frame that writes anything. Sending goes through the POST, which
is where idempotency, the audit trail and the email bridge live. A socket that
could write would be a second send path with none of that, and the two would
drift -- one implementation per concept (claude.md rule 5).

THE NOTIFICATION IS PUBLISHED BY THE COMMIT ITSELF
----------------------------------------------------
`get_tenant_db` holds the whole request in one transaction and commits when the
dependency exits. Publishing inline would announce a message that another API
instance cannot read yet, and on a rollback would announce one that never
existed at all -- every listening tab rendering a message that is not there.

`BackgroundTasks` does NOT solve this, and believing it does is the trap: in
FastAPI the response is sent, and therefore background tasks run, INSIDE the
dependency exit stack, so a background publish still fires before the commit.
`tests/test_conversations_api.py` asserts this from a second connection and
caught exactly that. So the publish hangs off SQLAlchemy's `after_commit`, which
is the one event that means what it says and does not depend on the web
framework's ordering.

WHY SENDING TO AN EMPLOYER IS REFUSED FROM HERE
------------------------------------------------
A BGV thread's outbound message is a verification act: it needs the employer's
address, the verification's status transition, an audit row and the MANAGE_BGV
capability, and it has its own route at `POST /bgv/verifications/{id}/send`.
This surface refuses it, so chat cannot become an unaudited way to contact a
candidate's former employer.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app.api.deps import ACCESS_COOKIE, CurrentUser, get_current_user, get_tenant_db, require_capability
from app.core.db import get_session_factory, tenant_scope
from app.core.security import AUDIENCE_ORG, decode_token
from app.models.conversation import CHANNEL_CHAT, KIND_BGV, MAX_BODY_CHARS, PARTY_RECRUITER
from app.models.enums import Role
from app.services import capabilities as caps
from app.services import conversations, object_storage, rbac, realtime

router = APIRouter()

#: Attachment ceiling. Bounded because an upload endpoint without one is a
#: storage bill somebody else controls.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: What may be attached. An ALLOWLIST, because a denylist is a list of the
#: formats somebody already thought of. Same argument `resume_storage` and
#: `document_storage` each make for their own narrower lists.
ALLOWED_ATTACHMENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
        "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

#: Long enough to click, short enough that a copied link is not a lasting grant.
ATTACHMENT_URL_TTL_SECONDS = 300

#: A page of history. Matched to what a chat pane renders in one scroll.
DEFAULT_PAGE = 50


# ── Shapes ───────────────────────────────────────────────────────────────────


class AttachmentOut(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    author_party: str
    author_user_id: uuid.UUID | None = None
    author_name: str | None = None
    body: str
    channel: str
    delivery_status: str
    delivery_detail: str | None = None
    created_at: datetime
    attachments: list[AttachmentOut] = Field(default_factory=list)


class ConversationOut(BaseModel):
    id: uuid.UUID
    kind: str
    subject: str
    status: str
    candidate_id: uuid.UUID | None = None
    bgv_verification_id: uuid.UUID | None = None
    last_message_at: datetime | None = None
    unread: int = 0


class SendMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    #: Minted by the client so a double click, a retry after a lost response and
    #: a reconnect that replays the send all collapse to ONE row. The server
    #: cannot derive this: every one of those arrives as a distinct request with
    #: identical content, and a content hash would refuse a candidate who
    #: legitimately wrote "yes" twice.
    client_token: str = Field(min_length=8, max_length=64)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _attachments_for(
    session: AsyncSession, message_ids: list[uuid.UUID]
) -> dict[str, list[AttachmentOut]]:
    """One query for the whole page, never one per message.

    A fifty-row page with a lookup per row is fifty round trips, and that shape
    is found by an audit rather than by anybody noticing.
    """
    if not message_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, message_id, filename, content_type, size_bytes "
                    "FROM conversation_attachments WHERE message_id = ANY(:ids)"
                ),
                {"ids": [str(value) for value in message_ids]},
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[str, list[AttachmentOut]] = {}
    for row in rows:
        grouped.setdefault(str(row["message_id"]), []).append(
            AttachmentOut(
                id=row["id"],
                filename=row["filename"],
                content_type=row["content_type"],
                size_bytes=row["size_bytes"],
            )
        )
    return grouped


async def _author_names(session: AsyncSession, rows: list[dict]) -> dict[str, str]:
    """Display names for the staff authors on this page, in one query.

    A message stores `author_name` only for an EXTERNAL author (an employer's HR
    contact, who has no user row). A staff author is resolved live, so somebody
    who has since corrected their name does not read as two different people
    across one thread.
    """
    ids = {str(row["author_user_id"]) for row in rows if row.get("author_user_id")}
    if not ids:
        return {}
    found = (
        (
            await session.execute(
                text("SELECT id, full_name, email FROM users WHERE id = ANY(:ids)"),
                {"ids": list(ids)},
            )
        )
        .mappings()
        .all()
    )
    return {
        str(row["id"]): (row["full_name"] or row["email"] or "A team member")
        for row in found
    }


def _to_message_out(
    row: dict,
    *,
    names: dict[str, str],
    attachments: list[AttachmentOut] | None = None,
) -> MessageOut:
    """One place that turns a stored row into the wire shape.

    Explicit field by field rather than `**row`: the row carries `client_token`,
    `email_message_id` and `tenant_id`, and a splat would quietly start
    serialising any column added to the table later.
    """
    author_user_id = row.get("author_user_id")
    return MessageOut(
        id=row["id"],
        conversation_id=row["conversation_id"],
        author_party=row["author_party"],
        author_user_id=author_user_id,
        author_name=(
            names.get(str(author_user_id))
            if author_user_id
            else row.get("author_name")
        ),
        body=row["body"],
        channel=row["channel"],
        delivery_status=row["delivery_status"],
        delivery_detail=row.get("delivery_detail"),
        created_at=row["created_at"],
        attachments=attachments or [],
    )


async def _load_conversation(
    session: AsyncSession, *, conversation_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict:
    try:
        return await conversations.authorize_participant(
            session, conversation_id=conversation_id, tenant_id=tenant_id
        )
    except conversations.ConversationNotFound:
        # 404, never 403: distinguishing them would confirm that another
        # customer holds this id to somebody who cannot read it.
        raise HTTPException(status_code=404, detail="Conversation not found") from None


# ── Listing and history ──────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[ConversationOut],
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def list_conversations(
    candidate_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[ConversationOut]:
    """This tenant's conversations, most recently active first."""
    params: dict = {"tid": str(user.tenant_id)}
    clause = ""
    if candidate_id is not None:
        clause = "AND c.candidate_id = :cand "
        params["cand"] = str(candidate_id)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id, c.kind, c.subject, c.status, c.candidate_id, "
                    " c.bgv_verification_id, c.last_message_at "
                    "FROM conversations c "
                    f"WHERE c.tenant_id = :tid {clause}"
                    "ORDER BY c.last_message_at DESC NULLS LAST, c.created_at DESC "
                    "LIMIT 200"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    unread = await conversations.unread_counts(
        session, tenant_id=user.tenant_id, user_id=user.user_id
    )
    return [
        ConversationOut(**dict(row), unread=unread.get(str(row["id"]), 0))
        for row in rows
    ]


@router.get(
    "/unread",
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def unread(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """The badge, and the per-thread breakdown behind it.

    Operational counts, not a candidate measurement: the no-numbers rule governs
    what is said ABOUT a candidate, and "three unread" says nothing about one.
    """
    counts = await conversations.unread_counts(
        session, tenant_id=user.tenant_id, user_id=user.user_id
    )
    return {"total": sum(counts.values()), "by_conversation": counts}


@router.post(
    "/candidate/{candidate_id}",
    response_model=ConversationOut,
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def open_candidate_conversation(
    candidate_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> ConversationOut:
    """Open the thread with this candidate, creating it once if it is new.

    The candidate must be linked to one of THIS tenant's jobs. Without that
    check a recruiter could open a thread with anybody in the databank by id,
    which is a cross-tenant read wearing a convenience feature's clothes.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.full_name FROM candidates c "
                    "WHERE c.id = :cid AND EXISTS ("
                    " SELECT 1 FROM job_candidate_links l "
                    "  WHERE l.candidate_id = c.id AND l.tenant_id = :tid)"
                ),
                {"cid": str(candidate_id), "tid": str(user.tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    conversation = await conversations.ensure_candidate_conversation(
        session,
        tenant_id=user.tenant_id,
        candidate_id=candidate_id,
        candidate_name=row["full_name"] or "Candidate",
        actor_user_id=user.user_id,
    )
    return ConversationOut(
        id=conversation["id"],
        kind=conversation["kind"],
        subject=conversation["subject"],
        status=conversation["status"],
        candidate_id=conversation["candidate_id"],
        bgv_verification_id=conversation["bgv_verification_id"],
        last_message_at=conversation["last_message_at"],
        unread=0,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageOut],
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def messages(
    conversation_id: uuid.UUID,
    before: datetime | None = None,
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[MessageOut]:
    """One page of history, oldest first within the page.

    Keyset paging on `created_at`, never an offset: new rows arrive here
    constantly by design, and an offset shifts under the reader, which
    duplicates or drops a message mid-scroll.
    """
    await _load_conversation(
        session, conversation_id=conversation_id, tenant_id=user.tenant_id
    )
    rows = await conversations.list_messages(
        session,
        conversation_id=conversation_id,
        tenant_id=user.tenant_id,
        limit=limit,
        before=before,
    )
    names = await _author_names(session, rows)
    files = await _attachments_for(session, [row["id"] for row in rows])
    return [
        _to_message_out(row, names=names, attachments=files.get(str(row["id"]), []))
        for row in rows
    ]


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> MessageOut:
    """Post one message. Persist first, publish after the commit."""
    conversation = await _load_conversation(
        session, conversation_id=conversation_id, tenant_id=user.tenant_id
    )
    if conversation["kind"] == KIND_BGV:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Messages to a previous employer are sent from the background "
                "verification panel, so the verification record and the email "
                "stay in step."
            ),
        )

    try:
        message = await conversations.post_message(
            session,
            conversation_id=conversation_id,
            tenant_id=user.tenant_id,
            author_party=PARTY_RECRUITER,
            author_user_id=user.user_id,
            body=body.body,
            channel=CHANNEL_CHAT,
            client_token=body.client_token,
        )
    except conversations.ConversationRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    names = await _author_names(session, [message])
    _publish_after_commit(
        session,
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        message=message,
        names=names,
    )
    return _to_message_out(message, names=names)


@router.post(
    "/{conversation_id}/read",
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def mark_read(
    conversation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    await _load_conversation(
        session, conversation_id=conversation_id, tenant_id=user.tenant_id
    )
    await conversations.mark_read(
        session,
        conversation_id=conversation_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    return {"ok": True}


def _publish_after_commit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message: dict,
    names: dict[str, str],
) -> None:
    """Announce this message the moment its transaction commits, never before.

    The mechanism lives in `services/realtime` because the inbound-email
    webhook needs the same guarantee from a different session, and two copies
    of an ordering rule is two places for it to stop being true.
    """
    realtime.publish_after_commit(
        session,
        realtime.message_event(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message={
                **message,
                "author_name": names.get(str(message.get("author_user_id")))
                or message.get("author_name"),
            },
        ),
    )


# ── Attachments ──────────────────────────────────────────────────────────────


@router.post(
    "/{conversation_id}/attachments",
    response_model=MessageOut,
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def upload_attachment(
    conversation_id: uuid.UUID,
    caption: str = Form(default=""),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> MessageOut:
    """One file becomes one message carrying one attachment.

    The object key is content-addressed and tenant-prefixed: a retry after a
    lost response resolves to the object already stored rather than writing a
    second one, and two customers never share an object even when they upload
    identical bytes.

    THE BYTES ARE STORED BEFORE THE ROW IS WRITTEN, and a storage failure
    RAISES. A message row with no file behind it renders an attachment that
    cannot be opened, which is worse than a refused upload.
    """
    await _load_conversation(
        session, conversation_id=conversation_id, tenant_id=user.tenant_id
    )

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"{content_type or 'That file type'} cannot be attached here.",
        )
    # Read ONE byte past the ceiling so an oversized file is refused rather than
    # silently truncated to the limit and stored as a corrupt document.
    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Attachments are limited to "
                f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB."
            ),
        )
    if not payload:
        raise HTTPException(status_code=422, detail="That file is empty.")

    filename = (file.filename or "attachment")[:300]
    digest = hashlib.sha256(payload).hexdigest()
    key = f"conversations/{user.tenant_id}/{digest}"
    try:
        # boto3 is synchronous and the threadpool hop belongs at the call site,
        # not hidden inside `object_storage` (that module's own rule).
        await run_in_threadpool(
            object_storage.put_if_absent,
            key=key,
            data=payload,
            content_type=content_type,
            metadata={"conversation": str(conversation_id)},
        )
    except object_storage.ObjectStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="The file could not be stored. Try again.",
        ) from exc

    attachment_id = uuid.uuid4()
    message = await conversations.post_message(
        session,
        conversation_id=conversation_id,
        tenant_id=user.tenant_id,
        author_party=PARTY_RECRUITER,
        author_user_id=user.user_id,
        body=(caption or "").strip() or f"Sent a file: {filename}",
        channel=CHANNEL_CHAT,
        # Derived from the CONTENT, so a retried upload of the same file by the
        # same person into the same thread is one message, not two.
        client_token=f"att-{digest[:48]}",
    )
    await session.execute(
        text(
            "INSERT INTO conversation_attachments (id, message_id, tenant_id, "
            " object_key, filename, content_type, size_bytes, created_at) "
            "VALUES (:id, :mid, :tid, :key, :name, :ctype, :size, now()) "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "id": str(attachment_id),
            "mid": str(message["id"]),
            "tid": str(user.tenant_id),
            "key": key,
            "name": filename,
            "ctype": content_type,
            "size": len(payload),
        },
    )
    names = await _author_names(session, [message])
    _publish_after_commit(
        session,
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        message=message,
        names=names,
    )
    # Re-read rather than trusting the insert: a retry that collapsed onto an
    # existing message must answer with the attachment that is actually there.
    files = await _attachments_for(session, [message["id"]])
    return _to_message_out(
        message, names=names, attachments=files.get(str(message["id"]), [])
    )


@router.get(
    "/attachments/{attachment_id}/url",
    dependencies=[Depends(require_capability(caps.USE_CONVERSATIONS))],
)
async def attachment_url(
    attachment_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """A short-lived URL for one attachment in THIS tenant.

    The object key never crosses the API boundary. A client holds an id and asks
    for a URL each time, so a bucket name is never something a browser knows and
    a copied link stops working in five minutes.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.object_key, a.filename, a.tenant_id "
                    "FROM conversation_attachments a WHERE a.id = :aid"
                ),
                {"aid": str(attachment_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None or str(row["tenant_id"]) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        url = await run_in_threadpool(
            object_storage.presigned_get_url,
            row["object_key"],
            ttl_seconds=ATTACHMENT_URL_TTL_SECONDS,
            filename=row["filename"],
        )
    except object_storage.ObjectStorageError as exc:
        raise HTTPException(
            status_code=503, detail="The file could not be opened. Try again."
        ) from exc
    return {"url": url, "expires_in": ATTACHMENT_URL_TTL_SECONDS}


# ── The socket ───────────────────────────────────────────────────────────────


def _identity_from_socket(websocket: WebSocket) -> CurrentUser | None:
    """(user, tenant) from the same cookie the REST routes read.

    A query-string token is accepted as a fallback because some proxies drop
    cookies from an upgrade request. It is validated IDENTICALLY, against the
    same org audience, so it grants nothing the cookie would not -- it is the
    same token by a different door, never a second, weaker credential.
    """
    token = websocket.cookies.get(ACCESS_COOKIE) or websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = decode_token(token, audience=AUDIENCE_ORG)
    except pyjwt.PyJWTError:
        return None
    if payload.get("type") != "access" or not payload.get("tenant_id"):
        return None
    try:
        return CurrentUser(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            role=Role(payload["role"]),
            audience=payload.get("aud"),
        )
    except (KeyError, ValueError):
        return None


@router.websocket("/{conversation_id}/stream")
async def stream(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Live updates for one conversation. READ ONLY, by construction.

    The three checks the REST routes make are all made here, in the same order:
    the token is a valid org access token, the caller holds USE_CONVERSATIONS,
    and the conversation belongs to their tenant. A socket is not a back door
    around a capability, so the capability is resolved live rather than trusted
    from the token, exactly as `require_capability` does.
    """
    user = _identity_from_socket(websocket)
    if user is None or user.tenant_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with get_session_factory()() as session:
        async with session.begin():
            async with tenant_scope(session, user.tenant_id):
                allowed = await rbac.has_capability(
                    session,
                    user.tenant_id,
                    user.role,
                    caps.USE_CONVERSATIONS,
                    user.user_id,
                )
                if not allowed:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                try:
                    await conversations.authorize_participant(
                        session,
                        conversation_id=conversation_id,
                        tenant_id=user.tenant_id,
                    )
                except conversations.ConversationNotFound:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

    await websocket.accept()
    queue = await realtime.hub.join(
        tenant_id=user.tenant_id, conversation_id=conversation_id
    )
    try:
        # A hello frame, so a client can tell "connected" apart from "connected
        # and nobody has typed" without waiting for a first message.
        await websocket.send_json(
            {
                "type": "ready",
                "conversation_id": str(conversation_id),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        # ALWAYS, on every exit path. A queue left registered is a slow leak
        # that ends with the reader fanning out to sockets nobody is holding.
        await realtime.hub.leave(
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            queue=queue,
        )

"""In-product customer support: both sides of one conversation.

    Customer Portal   /support/...            a client company's own threads
    Provider Portal   /provider/support/...   Vivekium's queue across customers

TWO ROUTERS IN ONE MODULE, AND WHY THAT IS NOT A DUAL CODE PATH
-----------------------------------------------------------------
They are two AUDIENCES of one feature, not two implementations of one
behaviour. Everything that decides anything is shared: `services/support` owns
the FSM, `_append_message` is the single write path, and the status a thread
lands in is DERIVED from who wrote rather than chosen by either handler. What
differs between the routers is exactly what must: which session they open, and
therefore what they can see.

Splitting them into two modules would have put the one write path in a third,
and joining them into one router would have meant a handler branching on the
caller's portal, which is the role branch this codebase forbids.

THE PROVIDER PREFIX IS `/provider`, NOT `/admin`
--------------------------------------------------
The brief wrote `/admin/support/...`. In this product the UI lives at `/admin`
and the Provider API lives at `/provider` (see `api/provider.py`'s own header
and `main.py`'s mounting); `/admin` is the Owner console's onboarding,
permissions and audit router. Support is a customer-MANAGEMENT view, so it
belongs beside the customer list. ASSUMPTION recorded rather than silently
chosen: the frontend route stays `/admin/support` and calls `/provider/support`,
which is what every other Provider screen already does.

AUTHORIZATION, AND THE ONE PLACE A CAPABILITY IS NOT THE GATE
---------------------------------------------------------------
The customer routes go through `require_capability(OPEN_SUPPORT_THREADS)`,
never a role name.

The Provider routes go through `get_superadmin_db`, which enforces the owner
AUDIENCE and the platform role, opens the RLS-bypass scope, and writes an
audit_log row for the cross-tenant access. That is the existing pattern for
every Provider cross-tenant read and it is not weaker than a capability check:
it is the same gate plus an audit row. `require_capability` cannot serve these
routes at all, because it resolves through `get_tenant_db` and a platform user
has no tenant to resolve against. `HANDLE_SUPPORT_THREADS` exists for the
notification routing in `workers/tasks.notify_support_message`, which asks the
permission ROWS who should be told rather than branching on a role name.

CROSS-TENANT IS A 404, NEVER A 403
------------------------------------
A customer asking for a thread id belonging to somebody else gets "not found",
because a 403 confirms the id exists. RLS makes this the natural answer rather
than something a handler has to remember: the row is simply not visible to that
session, so the lookup returns nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_superadmin_db,
    get_tenant_db,
    require_capability,
)
from app.models.support import (
    SIDE_CUSTOMER,
    SIDE_STAFF,
    THREAD_OPEN,
    SupportMessage,
    SupportThread,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.support import (
    MessageIn,
    ProviderThreadDetailOut,
    ProviderThreadListOut,
    ProviderThreadOut,
    SupportMessageOut,
    ThreadDetailOut,
    ThreadListOut,
    ThreadOpenIn,
    ThreadOut,
    ThreadPatchIn,
)
from app.services import support as support_fsm
from app.services.capabilities import OPEN_SUPPORT_THREADS
from app.workers.dispatch import dispatch

router = APIRouter()
provider_router = APIRouter()

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

#: The whole conversation is returned with a thread. A support thread is a
#: handful of messages, unlike an assessment transcript (up to 120), so paging
#: it would add a round trip to every read and save nothing. The ceiling is
#: stated rather than left as "however many there are".
MAX_MESSAGES_RETURNED = 200


# ── The one write path ───────────────────────────────────────────────────────

async def _append_message(
    session: AsyncSession,
    thread: SupportThread,
    *,
    author: CurrentUser,
    body: str,
    author_side: str,
) -> SupportMessage:
    """Write a message and move the thread. The ONLY place either happens.

    The status is DERIVED from `author_side` here rather than passed in by a
    handler, so "threads waiting on Vivekium" can never mean "threads somebody
    remembered to mark". A missed mark would be a customer waiting on a reply
    that nobody could see was owed, which is precisely the failure this surface
    exists to prevent.

    Assignment is claimed by REPLYING: the first staff member to answer owns it.
    Set only when unset, so a later replier does not take a colleague's thread
    out from under them.
    """
    now = datetime.now(timezone.utc)
    message = SupportMessage(
        thread_id=thread.id,
        tenant_id=thread.tenant_id,
        author_user_id=author.user_id,
        author_side=author_side,
        body=body,
    )
    session.add(message)

    thread.status = support_fsm.status_after_message(author_side)
    thread.last_message_at = now
    thread.updated_at = now
    if author_side == SIDE_STAFF and thread.assigned_to is None:
        thread.assigned_to = author.user_id

    await session.flush()
    return message


def _notify(thread: SupportThread, message: SupportMessage) -> None:
    """Hand the notification to a background task, never send it here.

    Dispatched because a request handler must not wait on SMTP or SES (rule 4),
    and `dispatch` RAISES on failure rather than degrading, so a queue that did
    not accept the work cannot be reported as a notification that was sent.
    """
    dispatch(
        "pickready.notify_support_message",
        args=[str(thread.id), str(message.id)],
    )


# ── Serialisation ────────────────────────────────────────────────────────────

async def _message_rows(
    session: AsyncSession, thread_id: uuid.UUID
) -> list[SupportMessageOut]:
    rows = (
        await session.execute(
            select(SupportMessage, User.full_name)
            .outerjoin(User, User.id == SupportMessage.author_user_id)
            .where(SupportMessage.thread_id == thread_id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
            .limit(MAX_MESSAGES_RETURNED)
        )
    ).all()
    return [
        SupportMessageOut(
            id=message.id,
            author_side=message.author_side,
            author_name=name,
            body=message.body,
            created_at=message.created_at,
        )
        for message, name in rows
    ]


async def _counts_by_thread(
    session: AsyncSession, thread_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """One grouped query for the whole page, never one per row.

    A per-row count is the shape that makes a list screen slow in a way that
    only appears once a customer has been using the product for a while.
    """
    if not thread_ids:
        return {}
    rows = await session.execute(
        select(SupportMessage.thread_id, func.count())
        .where(SupportMessage.thread_id.in_(thread_ids))
        .group_by(SupportMessage.thread_id)
    )
    return {thread_id: count for thread_id, count in rows}


def _paginate(query: Select, page: int, page_size: int) -> Select:
    return query.offset((page - 1) * page_size).limit(page_size)


# ═══════════════════════════════════════════════════════════════════════════
# Customer Portal
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/threads", response_model=ThreadDetailOut, status_code=201)
async def open_thread(
    body: ThreadOpenIn,
    user: CurrentUser = Depends(require_capability(OPEN_SUPPORT_THREADS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ThreadDetailOut:
    """Start a conversation. Subject and first message in one call.

    `tenant_id` comes from the SESSION's user, never from the request body: a
    tenant id a caller can supply is a tenant id a caller can change.
    """
    thread = SupportThread(
        tenant_id=user.tenant_id,
        opened_by_user_id=user.user_id,
        subject=body.subject,
        status=THREAD_OPEN,
    )
    session.add(thread)
    await session.flush()

    message = await _append_message(
        session, thread, author=user, body=body.body, author_side=SIDE_CUSTOMER
    )
    _notify(thread, message)

    return ThreadDetailOut(
        id=thread.id,
        subject=thread.subject,
        status=thread.status,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        message_count=1,
        messages=await _message_rows(session, thread.id),
    )


@router.get("/threads", response_model=ThreadListOut)
async def list_threads(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: CurrentUser = Depends(require_capability(OPEN_SUPPORT_THREADS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ThreadListOut:
    """The caller's own tenant's threads, newest activity first.

    No tenant filter is written here. RLS is the boundary (rule 1), and a
    redundant WHERE would invite the next reader to believe the WHERE is what
    protects them and to omit it somewhere it matters.
    """
    total = int(
        (
            await session.execute(select(func.count()).select_from(SupportThread))
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                _paginate(
                    select(SupportThread).order_by(
                        SupportThread.last_message_at.desc(), SupportThread.id
                    ),
                    page,
                    page_size,
                )
            )
        )
        .scalars()
        .all()
    )
    counts = await _counts_by_thread(session, [t.id for t in rows])
    return ThreadListOut(
        items=[
            ThreadOut(
                id=t.id,
                subject=t.subject,
                status=t.status,
                created_at=t.created_at,
                last_message_at=t.last_message_at,
                message_count=counts.get(t.id, 0),
            )
            for t in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
async def read_thread(
    thread_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(OPEN_SUPPORT_THREADS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ThreadDetailOut:
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        # 404 and not 403: a 403 confirms the id exists, which tells somebody
        # probing that they have found a real thread belonging to a competitor.
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = await _message_rows(session, thread.id)
    return ThreadDetailOut(
        id=thread.id,
        subject=thread.subject,
        status=thread.status,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        message_count=len(messages),
        messages=messages,
    )


@router.post(
    "/threads/{thread_id}/messages",
    response_model=SupportMessageOut,
    status_code=201,
)
async def reply_as_customer(
    thread_id: uuid.UUID,
    body: MessageIn,
    user: CurrentUser = Depends(require_capability(OPEN_SUPPORT_THREADS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SupportMessageOut:
    """Reply. A resolved thread reopens, which is why `resolved` is not
    terminal: forcing a follow-up question into a new thread would lose the
    history that made it answerable."""
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    message = await _append_message(
        session, thread, author=user, body=body.body, author_side=SIDE_CUSTOMER
    )
    _notify(thread, message)
    return SupportMessageOut(
        id=message.id,
        author_side=message.author_side,
        author_name=None,
        body=message.body,
        created_at=message.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Provider Portal
# ═══════════════════════════════════════════════════════════════════════════

async def _provider_names(
    session: AsyncSession, threads: Sequence[SupportThread]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """Tenant and assignee names for a PAGE of threads, in two statements.

    Was one `session.get` per tenant plus one per assignee, per row: a page of
    twenty-five threads across twenty-five customers cost up to fifty queries
    to render names. The N+1 shape, caught by this session's own audit sweep
    a day after being written, which is why the sweep exists.
    """
    tenant_ids = {thread.tenant_id for thread in threads}
    user_ids = {thread.assigned_to for thread in threads if thread.assigned_to}
    tenant_names: dict[uuid.UUID, str] = {}
    user_names: dict[uuid.UUID, str] = {}
    if tenant_ids:
        rows = await session.execute(
            select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenant_ids))
        )
        tenant_names = {row.id: row.name or "" for row in rows}
    if user_ids:
        rows = await session.execute(
            select(User.id, User.full_name).where(User.id.in_(user_ids))
        )
        user_names = {row.id: row.full_name for row in rows}
    return tenant_names, user_names


def _provider_row(
    thread: SupportThread,
    tenant_names: dict[uuid.UUID, str],
    user_names: dict[uuid.UUID, str],
) -> ProviderThreadOut:
    return ProviderThreadOut(
        id=thread.id,
        subject=thread.subject,
        status=thread.status,
        created_at=thread.created_at,
        last_message_at=thread.last_message_at,
        tenant_id=thread.tenant_id,
        tenant_name=tenant_names.get(thread.tenant_id, ""),
        assigned_to=thread.assigned_to,
        assigned_to_name=user_names.get(thread.assigned_to)
        if thread.assigned_to
        else None,
    )


@provider_router.get("/threads", response_model=ProviderThreadListOut)
async def provider_list_threads(
    tenant_id: uuid.UUID | None = Query(None),
    thread_status: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_superadmin_db),
) -> ProviderThreadListOut:
    """Vivekium's queue across every customer.

    Filtering, ordering and pagination all run in SQL, before the page is cut.
    Filtering a fetched page in the browser makes the match count depend on
    which page happened to be loaded, which is the defect the customer list
    already records.
    """
    if thread_status is not None:
        # Refused rather than silently ignored. An unrecognised filter that
        # returned everything would read as "no threads match" being wrong.
        #
        # 422 and not a bare raise: an unrecognised query parameter is the
        # CALLER's mistake, and letting `IllegalThreadTransition` escape makes
        # it a 500, which sends somebody reading the error to look for an
        # outage. The FSM raises because refusing is its job; converting the
        # refusal into the right status code is the route's.
        try:
            support_fsm.assert_status(thread_status)
        except support_fsm.IllegalThreadTransition as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    base = select(SupportThread)
    if tenant_id is not None:
        base = base.where(SupportThread.tenant_id == tenant_id)
    if thread_status is not None:
        base = base.where(SupportThread.status == thread_status)

    total = int(
        (
            await session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
    )
    # UNNARROWED by the filters on purpose: it answers "how much is owed", not
    # "how much is on the screen you happen to be looking at".
    open_total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SupportThread)
                .where(SupportThread.status == THREAD_OPEN)
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                _paginate(
                    base.order_by(
                        SupportThread.last_message_at.desc(), SupportThread.id
                    ),
                    page,
                    page_size,
                )
            )
        )
        .scalars()
        .all()
    )
    counts = await _counts_by_thread(session, [t.id for t in rows])
    tenant_names, user_names = await _provider_names(session, rows)

    items: list[ProviderThreadOut] = []
    for thread in rows:
        row = _provider_row(thread, tenant_names, user_names)
        row.message_count = counts.get(thread.id, 0)
        items.append(row)

    return ProviderThreadListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        open_total=open_total,
    )


@provider_router.get("/threads/{thread_id}", response_model=ProviderThreadDetailOut)
async def provider_read_thread(
    thread_id: uuid.UUID,
    session: AsyncSession = Depends(get_superadmin_db),
) -> ProviderThreadDetailOut:
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    tenant_names, user_names = await _provider_names(session, [thread])
    row = _provider_row(thread, tenant_names, user_names)
    messages = await _message_rows(session, thread.id)
    return ProviderThreadDetailOut(
        **row.model_dump(exclude={"message_count"}),
        message_count=len(messages),
        messages=messages,
    )


@provider_router.post(
    "/threads/{thread_id}/messages",
    response_model=SupportMessageOut,
    status_code=201,
)
async def provider_reply(
    thread_id: uuid.UUID,
    body: MessageIn,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_superadmin_db),
) -> SupportMessageOut:
    """Answer a customer. Claims the thread if nobody has it yet."""
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    message = await _append_message(
        session, thread, author=user, body=body.body, author_side=SIDE_STAFF
    )
    _notify(thread, message)
    return SupportMessageOut(
        id=message.id,
        author_side=message.author_side,
        author_name=None,
        body=message.body,
        created_at=message.created_at,
    )


@provider_router.patch("/threads/{thread_id}", response_model=ProviderThreadOut)
async def provider_patch_thread(
    thread_id: uuid.UUID,
    body: ThreadPatchIn,
    session: AsyncSession = Depends(get_superadmin_db),
) -> ProviderThreadOut:
    """Move a thread by hand. The FSM refuses a move it does not allow, naming
    both ends, rather than accepting it and leaving the queue wrong."""
    thread = await session.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        support_fsm.assert_transition(thread.status, body.status)
    except support_fsm.IllegalThreadTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    thread.status = body.status
    thread.updated_at = datetime.now(timezone.utc)
    await session.flush()
    tenant_names, user_names = await _provider_names(session, [thread])
    return _provider_row(thread, tenant_names, user_names)

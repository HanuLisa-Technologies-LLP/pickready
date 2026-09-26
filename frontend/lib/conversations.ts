// The conversations client: the REST calls, and the live socket.
//
// WHY THE SOCKET IS A HINT AND THE FETCH IS THE TRUTH
// The server writes a message to Postgres and then announces it. So a dropped
// frame, a sleeping background tab, a network change and a deploy that moves
// the connection to another API task are all the same event here, and none of
// them can lose a message: every (re)connect refetches the thread. Building it
// the other way round, with the socket as the delivery mechanism, would mean
// inventing acknowledgements in a browser.
//
// WHY A FAILED SOCKET IS SHOWN RATHER THAN PAPERED OVER
// The Next.js same-origin API proxy is a route handler, and a route handler
// never sees a WebSocket upgrade, so the socket connects in a deployed
// environment (where the load balancer routes /api/* straight to the API and
// upgrades natively) and does NOT connect against a local `next dev`. That is a
// real difference between environments, so the UI SAYS which mode it is in and
// keeps the thread current by refetching. A degradation is recorded, never
// silent (claude.md, 2026-09-09); a chat that quietly stopped updating would be
// indistinguishable from a chat nobody had written in.

import { API_BASE, apiGet, apiPost, apiUpload, tryRefresh } from "./api";

export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  author_party: "recruiter" | "candidate" | "employer_hr" | "system";
  author_user_id: string | null;
  author_name: string | null;
  body: string;
  channel: "chat" | "email";
  delivery_status: string;
  delivery_detail: string | null;
  created_at: string;
  attachments: Attachment[];
}

export interface Conversation {
  id: string;
  kind: "candidate" | "bgv";
  subject: string;
  status: string;
  candidate_id: string | null;
  bgv_verification_id: string | null;
  last_message_at: string | null;
  unread: number;
}

export const openCandidateConversation = (candidateId: string) =>
  apiPost<Conversation>(`/conversations/candidate/${candidateId}`);

export const listMessages = (conversationId: string, limit = 50) =>
  apiGet<Message[]>(`/conversations/${conversationId}/messages?limit=${limit}`);

/**
 * The page before `oldest`. The server pages on the pair (created_at, id), so
 * two messages sharing a timestamp are neither skipped nor repeated; sending
 * the timestamp alone would drop whichever of them sat on the page boundary.
 */
export const listMessagesBefore = (
  conversationId: string,
  before: string,
  beforeId?: string,
) =>
  apiGet<Message[]>(
    `/conversations/${conversationId}/messages?${pageQuery(before, beforeId)}`,
  );

/** How many messages one history request returns (`DEFAULT_PAGE` server-side).
 *  A page SHORTER than this is the start of the thread. */
export const MESSAGE_PAGE_SIZE = 50;

function pageQuery(before?: string, beforeId?: string): string {
  const params = new URLSearchParams({ limit: String(MESSAGE_PAGE_SIZE) });
  if (before) params.set("before", before);
  if (before && beforeId) params.set("before_id", beforeId);
  return params.toString();
}

// ── The candidate's side ─────────────────────────────────────────────────────
//
// A second set of routes under /conversations/me, never the recruiter's with a
// different cookie: a candidate has no tenant, and the server resolves the
// thread by the candidate id from their own session.

/** One thread as the candidate sees it. `unread` counts the company's messages
 *  the candidate has not opened yet; it goes down when they read the thread. */
export interface CandidateThread {
  id: string;
  subject: string;
  status: string;
  company_name: string | null;
  last_message_at: string | null;
  unread: number;
}

export const listMyThreads = () =>
  apiGet<CandidateThread[]>("/conversations/me");

export const listMyMessages = (
  conversationId: string,
  oldest?: { created_at: string; id: string },
) =>
  apiGet<Message[]>(
    `/conversations/me/${conversationId}/messages?${pageQuery(
      oldest?.created_at,
      oldest?.id,
    )}`,
  );

export const sendMyReply = (
  conversationId: string,
  body: string,
  clientToken: string,
) =>
  apiPost<Message>(`/conversations/me/${conversationId}/messages`, {
    body,
    client_token: clientToken,
  });

export const markMyThreadRead = (conversationId: string) =>
  apiPost<{ ok: boolean }>(`/conversations/me/${conversationId}/read`);

export const myUnreadCount = () =>
  apiGet<{ unread_count: number }>("/conversations/me/unread");

/**
 * Tell the navigation badge that the candidate just read something.
 *
 * The badge lives in the portal layout and the thread in the page beneath it;
 * a window event is the whole coupling between them, so neither imports the
 * other and a page with no badge on screen costs nothing.
 */
export const UNREAD_CHANGED_EVENT = "vivekium:messages-unread-changed";

export function announceUnreadChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(UNREAD_CHANGED_EVENT));
}

/**
 * Two slices of one thread, as one ordered list with no repeats.
 *
 * Used when a page of older messages is prepended and when a refetch of the
 * newest page lands on top of history already loaded. Ordered exactly as the
 * server pages, (created_at, id), so a merge can never reorder what a later
 * "load earlier" will fetch.
 */
export function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  const byId = new Map<string, Message>();
  for (const message of current) byId.set(message.id, message);
  // The incoming copy wins: a delivery status can move after the first read.
  for (const message of incoming) byId.set(message.id, message);
  const order = (x: string, y: string) => (x < y ? -1 : x > y ? 1 : 0);
  return [...byId.values()].sort(
    (a, b) => order(a.created_at, b.created_at) || order(a.id, b.id),
  );
}

export const sendMessage = (
  conversationId: string,
  body: string,
  clientToken: string,
) =>
  apiPost<Message>(`/conversations/${conversationId}/messages`, {
    body,
    client_token: clientToken,
  });

export const markConversationRead = (conversationId: string) =>
  apiPost<{ ok: boolean }>(`/conversations/${conversationId}/read`);

export function uploadAttachment(
  conversationId: string,
  file: File,
  caption: string,
) {
  const form = new FormData();
  form.append("file", file);
  form.append("caption", caption);
  return apiUpload<Message>(`/conversations/${conversationId}/attachments`, form);
}

export const attachmentUrl = (attachmentId: string) =>
  apiGet<{ url: string; expires_in: number }>(
    `/conversations/attachments/${attachmentId}/url`,
  );

/**
 * A client token that is stable for one composed message and different for the
 * next one. The server collapses duplicates on it, so a double click, a retry
 * after a lost response and a reconnect that replays the send are one message.
 */
export function newClientToken(): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `c-${random}`.slice(0, 64);
}

// The idempotency token for one composed message lives in
// `lib/composer-token.ts`: one implementation, used by the recruiter's panel
// and the candidate's Messages page alike.

/** Bytes, spelled for a person. Never a raw byte count on screen. */
export function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type StreamState = "connecting" | "live" | "offline";

interface StreamHandlers {
  onMessage: (message: Message) => void;
  /** Fired on every successful connect, including a reconnect, so the caller
   *  can refetch whatever arrived while the socket was down. */
  onSync: () => void;
  onState: (state: StreamState) => void;
}

/** Backoff between reconnects. Capped, and with the first retry fast enough
 *  that an ordinary blip is invisible. */
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 30_000];

/** While the socket is down the thread is kept current by refetching. Slower
 *  than a socket on purpose: this is the degraded mode, not a second design. */
export const OFFLINE_REFRESH_MS = 15_000;

function socketUrl(conversationId: string): string {
  const base = API_BASE.startsWith("http")
    ? new URL(API_BASE)
    : new URL(API_BASE, window.location.origin);
  const scheme = base.protocol === "https:" ? "wss:" : "ws:";
  const path = `${base.pathname.replace(/\/$/, "")}/conversations/${conversationId}/stream`;
  return `${scheme}//${base.host}${path}`;
}

/**
 * Hold a live connection to one conversation until the returned function is
 * called. Reconnects with backoff; refreshes the session once on a policy
 * close, because an access cookie that lapsed mid-session is the ordinary
 * reason a reconnect is refused and bouncing the user to the login screen for
 * it would be wrong.
 */
export function openConversationStream(
  conversationId: string,
  handlers: StreamHandlers,
): () => void {
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let closed = false;
  let refreshedOnce = false;

  const schedule = (policyRefusal: boolean) => {
    if (closed) return;
    handlers.onState("offline");
    const wait = RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)];
    attempt += 1;
    timer = setTimeout(() => {
      if (closed) return;
      if (policyRefusal && !refreshedOnce) {
        // ONCE. A refresh loop against a genuinely revoked session would hammer
        // the auth endpoint for as long as the tab stayed open.
        refreshedOnce = true;
        void tryRefresh().then(connect);
        return;
      }
      connect();
    }, wait);
  };

  function connect(): void {
    if (closed) return;
    handlers.onState(attempt === 0 ? "connecting" : "offline");
    let opened: WebSocket;
    try {
      opened = new WebSocket(socketUrl(conversationId));
    } catch {
      // A URL the browser refuses outright (an unsupported scheme behind a
      // proxy, for instance). Treated exactly like a failed connection.
      schedule(false);
      return;
    }
    socket = opened;

    opened.onopen = () => {
      attempt = 0;
      refreshedOnce = false;
      handlers.onState("live");
      // Refetch on EVERY connect, not only the first: the gap while the socket
      // was down is precisely when a message can have been missed.
      handlers.onSync();
    };

    opened.onmessage = (event) => {
      let payload: { type?: string; message?: Message };
      try {
        payload = JSON.parse(event.data as string);
      } catch {
        return;
      }
      if (payload.type === "message" && payload.message) {
        handlers.onMessage(payload.message);
      }
    };

    // No `onerror` handler: `onclose` always follows one, and that is where the
    // retry is decided. Retrying in both would schedule two reconnects for one
    // failure and halve the backoff that exists to protect the API.

    opened.onclose = (event) => {
      socket = null;
      schedule(event.code === 1008);
    };
  }

  connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
  };
}

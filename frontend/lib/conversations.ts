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

export const listMessagesBefore = (conversationId: string, before: string) =>
  apiGet<Message[]>(
    `/conversations/${conversationId}/messages?before=${encodeURIComponent(before)}`,
  );

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

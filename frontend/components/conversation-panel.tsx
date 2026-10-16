"use client";

// One conversation, live.
//
// WHY THE SOCKET'S STATE IS ON SCREEN
// A chat that has quietly stopped updating is indistinguishable from a chat
// nobody has written in, and the person looking at it has no way to tell. So
// the connection says which of the two it is, and when it is down the thread is
// kept current by refetching rather than by hoping. A degradation is recorded,
// never silent (claude.md, 2026-09-09).
//
// WHY A SENT MESSAGE IS RECONCILED BY ID AND NOT APPENDED
// The same message arrives twice by design: once as the POST's response and
// once through the socket. Both carry the server's id, so the list is keyed on
// it and the second arrival replaces the first rather than doubling it. A
// client-side "did I send this" flag would get this wrong the moment a second
// recruiter had the same thread open.
//
// WHY A RETRY CARRIES THE SAME CLIENT TOKEN
// The server collapses duplicate sends on `client_token`, so the token belongs
// to the DRAFT, not to the click (`lib/composer-token.ts`). A failed send keeps
// it, a confirmed send or an edit to the text rotates it. Minting one per
// attempt, as this panel did until the vivekium release, turned a send whose
// response was lost into two messages the moment the recruiter pressed Send
// again.
//
// WHY AN EMPLOYER THREAD IS READ ONLY HERE
// Writing to one is a verification act with its own capability and its own
// status transition, and the server refuses it from this surface. The compose
// box is hidden rather than rendered and then refused, because a control that
// looks available and is not is the specific way a gate becomes infuriating.

import * as React from "react";
import { Loader2, Paperclip, RefreshCw, Send, Wifi, WifiOff } from "lucide-react";
import { InlineError } from "@/components/page-primitives";

import {
  type Message,
  type StreamState,
  OFFLINE_REFRESH_MS,
  attachmentUrl,
  listMessages,
  listMessagesBefore,
  markConversationRead,
  openConversationStream,
  readableSize,
  sendMessage,
  uploadAttachment,
} from "@/lib/conversations";
import { useComposerToken } from "@/lib/composer-token";
import { apiErrorMessage } from "@/lib/validation-errors";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";

const PARTY_LABEL: Record<Message["author_party"], string> = {
  recruiter: "Your team",
  candidate: "Candidate",
  employer_hr: "Previous employer",
  system: "Vivekium",
};

const STATE_LABEL: Record<StreamState, string> = {
  connecting: "Connecting",
  live: "Live",
  offline: "Reconnecting, refreshing meanwhile",
};

/** One page, matching the server's default. Used to decide whether asking for
 *  an earlier page could return anything. */
const PAGE = 50;

function byOldestFirst(messages: Message[]): Message[] {
  return [...messages].sort((left, right) =>
    left.created_at === right.created_at
      ? left.id.localeCompare(right.id)
      : left.created_at.localeCompare(right.created_at),
  );
}

/** Merge on the SERVER's id, so the POST response and the socket frame for one
 *  message are one row however they are ordered. */
function merge(existing: Message[], arriving: Message[]): Message[] {
  const byId = new Map(existing.map((message) => [message.id, message]));
  for (const message of arriving) {
    const previous = byId.get(message.id);
    byId.set(
      message.id,
      // The fetched copy carries attachments; a socket frame does not. Keeping
      // the richer one means an attachment does not vanish when the live frame
      // for the same message arrives second.
      previous && previous.attachments.length && !message.attachments.length
        ? { ...message, attachments: previous.attachments }
        : message,
    );
  }
  return byOldestFirst([...byId.values()]);
}

export function ConversationPanel({
  conversationId,
  readOnly = false,
  emptyCopy = "No messages yet.",
}: {
  conversationId: string;
  readOnly?: boolean;
  emptyCopy?: string;
}) {
  const { toast } = useToast();
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [state, setState] = React.useState<StreamState>("connecting");
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [hasMore, setHasMore] = React.useState(false);
  const endRef = React.useRef<HTMLDivElement | null>(null);
  const fileRef = React.useRef<HTMLInputElement | null>(null);
  const composerToken = useComposerToken();

  const sync = React.useCallback(async () => {
    try {
      const page = await listMessages(conversationId, PAGE);
      setMessages((current) => merge(current, page));
      setHasMore(page.length >= PAGE);
      setLoadError(null);
      await markConversationRead(conversationId);
    } catch (error) {
      setLoadError(apiErrorMessage(error));
    }
  }, [conversationId]);

  React.useEffect(() => {
    setMessages([]);
    void sync();
    const close = openConversationStream(conversationId, {
      onMessage: (message) => setMessages((current) => merge(current, [message])),
      onSync: () => void sync(),
      onState: setState,
    });
    return close;
  }, [conversationId, sync]);

  // The degraded mode, and ONLY the degraded mode. While the socket is live
  // this interval does not exist, so a healthy connection never pays for a
  // poll that has nothing to find.
  React.useEffect(() => {
    if (state !== "offline") return;
    const timer = setInterval(() => void sync(), OFFLINE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [state, sync]);

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length]);

  async function loadOlder() {
    if (!messages.length) return;
    setBusy(true);
    try {
      const older = await listMessagesBefore(conversationId, messages[0].created_at);
      setMessages((current) => merge(current, older));
      setHasMore(older.length >= PAGE);
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    try {
      const sent = await sendMessage(conversationId, body, composerToken.current());
      setMessages((current) => merge(current, [sent]));
      // Confirmed: the next thing typed is a new message.
      composerToken.rotate();
      setDraft("");
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  async function attach(file: File) {
    setBusy(true);
    try {
      const sent = await uploadAttachment(conversationId, file, draft.trim());
      setMessages((current) => merge(current, [sent]));
      setDraft("");
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function openAttachment(attachmentId: string) {
    try {
      const { url } = await attachmentUrl(attachmentId);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium">
          {state === "live" ? (
            <Wifi className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {STATE_LABEL[state]}
        </span>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void sync()}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="ml-1">Refresh</span>
        </Button>
      </div>

      {loadError ? <InlineError>{loadError}</InlineError> : null}

      <div className="max-h-96 space-y-3 overflow-y-auto rounded-md border p-3">
        {hasMore ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void loadOlder()}
          >
            Load earlier messages
          </Button>
        ) : null}

        {messages.length === 0 && !loadError ? (
          <p className="text-sm">{emptyCopy}</p>
        ) : null}

        {messages.map((message) => (
          <article key={message.id} className="space-y-1">
            <p className="text-xs font-medium">
              {message.author_name || PARTY_LABEL[message.author_party]}
              <span className="ml-2 font-normal">
                {new Date(message.created_at).toLocaleString()}
              </span>
              {message.channel === "email" ? (
                <span className="ml-2 font-normal">by email</span>
              ) : null}
            </p>
            <p className="whitespace-pre-wrap text-sm">{message.body}</p>

            {message.attachments.length ? (
              <ul className="flex flex-wrap gap-2">
                {message.attachments.map((file) => (
                  <li key={file.id}>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void openAttachment(file.id)}
                    >
                      <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
                      <span className="ml-1">
                        {file.filename} ({readableSize(file.size_bytes)})
                      </span>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}

            {/* The server's own word for what happened to an outbound email. A
                message that bounced and a message that arrived look the same on
                a screen that does not say. */}
            {message.delivery_status !== "delivered" ? (
              <p className="text-xs">
                Delivery: {message.delivery_status.replace(/_/g, " ")}
                {message.delivery_detail ? ` (${message.delivery_detail})` : ""}
              </p>
            ) : null}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      {readOnly ? (
        <p className="text-xs">
          Messages to a previous employer are sent from the background
          verification panel, so the verification record and the email stay in
          step.
        </p>
      ) : (
        <div className="space-y-2">
          <Textarea
            rows={3}
            value={draft}
            disabled={busy}
            placeholder="Write a message"
            aria-label="Message"
            onChange={(event) => {
              // Different words are a different message, even if the
              // previous attempt secretly reached the server.
              composerToken.rotate();
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              // Enter sends, Shift with Enter writes a new line. The ordinary
              // chat contract; a Send button alone makes every reply a mouse
              // journey.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={busy || !draft.trim()}
              onClick={() => void submit()}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              <span className="ml-1">Send</span>
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="ml-1">Attach a file</span>
            </Button>
            <input
              ref={fileRef}
              type="file"
              className="sr-only"
              tabIndex={-1}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void attach(file);
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

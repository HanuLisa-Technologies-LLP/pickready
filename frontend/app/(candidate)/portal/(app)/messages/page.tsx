"use client";

// The candidate's side of the conversation.
//
// WHY THIS PAGE EXISTS AT ALL
// A thread only the recruitment team can write to is not a conversation, it is
// an outbox. The recruiter's screen and this one read and write the same rows,
// so what a candidate sees here is exactly what was sent, in the order it was
// sent, with nothing summarised on the way.
//
// WHAT A CANDIDATE CANNOT REACH FROM HERE
// The background-verification threads. Those are a recruiter and a previous
// employer's HR contact discussing this person's employment history: the
// candidate is the subject, not a participant. The server refuses them by kind
// as well as by owner, and there is no control here that asks for one.
//
// WHY EACH THREAD NAMES ITS COMPANY
// A candidate's threads span employers. A list showing only subjects would be a
// list of indistinguishable rows, and replying to the wrong company about a
// notice period is a real and embarrassing failure.
//
// WHY THERE IS NO SOCKET HERE
// A candidate reads a thread when they come to it, not while they sit on it.
// The recruiter's screen holds a conversation open for an hour and a live
// connection earns its cost there; here the thread loads on open, on demand,
// and again whenever the window regains focus.
//
// THREE THINGS THIS PAGE OWES THE REST OF THE PORTAL
// * Opening a thread marks it read, and says so to the navigation badge.
// * `?conversation=<id>` (the Updates entry for a new message) opens that
//   thread rather than whichever came first.
// * A retry of an unsent reply reuses that thread's reply token
//   (`ComposerToken`), so a send whose response was lost cannot become a
//   second message. Drafts and tokens are kept per thread.

import * as React from "react";
import { useSearchParams } from "next/navigation";
import {
  Building2,
  ChevronUp,
  Loader2,
  MessagesSquare,
  RefreshCw,
  Send,
} from "lucide-react";

import { apiErrorMessage } from "@/lib/validation-errors";
import {
  MESSAGE_PAGE_SIZE,
  announceUnreadChanged,
  listMyMessages,
  listMyThreads,
  markMyThreadRead,
  mergeMessages,
  sendMyReply,
  type CandidateThread,
  type Message,
} from "@/lib/conversations";
import { createComposerToken, type ComposerToken } from "@/lib/composer-token";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { InlineError, LoadingRows } from "@/components/page-primitives";

const PARTY_LABEL: Record<string, string> = {
  recruiter: "Recruitment team",
  candidate: "You",
  system: "Vivekium",
};

export default function CandidateMessagesPage() {
  // `useSearchParams` needs a Suspense boundary above it, or the whole page
  // opts out of static rendering at build time.
  return (
    <React.Suspense
      fallback={<LoadingRows rows={3} label="Loading your conversations" />}
    >
      <MessagesView />
    </React.Suspense>
  );
}

function MessagesView() {
  const requested = useSearchParams().get("conversation");
  const { toast } = useToast();
  // One reply token PER THREAD, like the drafts below: switching threads and
  // back to an unsent reply must retry it under the same token, and a token
  // minted for one thread must never travel with words sent to another.
  const composers = React.useRef(new Map<string, ComposerToken>());
  const composerFor = React.useCallback((threadId: string): ComposerToken => {
    let token = composers.current.get(threadId);
    if (!token) {
      token = createComposerToken();
      composers.current.set(threadId, token);
    }
    return token;
  }, []);
  const [threads, setThreads] = React.useState<CandidateThread[] | null>(null);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [hasEarlier, setHasEarlier] = React.useState(false);
  const [loadingEarlier, setLoadingEarlier] = React.useState(false);
  // One draft PER THREAD. A single shared box carried words written to one
  // company into the next thread selected, one Enter away from replying to
  // the wrong employer about a notice period.
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  const draft = selected ? drafts[selected] ?? "" : "";
  const setDraft = React.useCallback(
    (value: string) => {
      if (!selected) return;
      setDrafts((current) => ({ ...current, [selected]: value }));
    },
    [selected],
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const scrollerRef = React.useRef<HTMLDivElement | null>(null);
  const endRef = React.useRef<HTMLDivElement | null>(null);
  // Set just before a page of OLDER messages is prepended, so the reader stays
  // on the message they were looking at instead of being thrown to the top.
  const keepOffsetRef = React.useRef<number | null>(null);

  const loadThreads = React.useCallback(async () => {
    try {
      const rows = await listMyThreads();
      setThreads(rows);
      setError(null);
      setSelected((current) => {
        if (current) return current;
        if (requested && rows.some((row) => row.id === requested)) {
          return requested;
        }
        return rows[0]?.id ?? null;
      });
    } catch (failure) {
      setThreads((current) => current ?? []);
      setError(apiErrorMessage(failure));
    }
  }, [requested]);

  /** The candidate has now seen this thread up to the newest message. */
  const markRead = React.useCallback(async (conversationId: string) => {
    try {
      await markMyThreadRead(conversationId);
    } catch (failure) {
      // The badge keeps counting this thread, which is the truthful state
      // when the server did not record the read. Said, not swallowed.
      setError(`This conversation could not be marked as read. ${apiErrorMessage(failure)}`);
      return;
    }
    setThreads((current) =>
      current?.map((thread) =>
        thread.id === conversationId ? { ...thread, unread: 0 } : thread,
      ) ?? current,
    );
    announceUnreadChanged();
  }, []);

  /** The newest page. `replace` on opening a thread; a merge on a refetch, so
   *  history already paged in with "Load earlier" is kept. */
  const loadLatest = React.useCallback(
    async (conversationId: string, replace: boolean) => {
      try {
        const page = await listMyMessages(conversationId);
        if (replace) {
          setMessages(page);
          setHasEarlier(page.length >= MESSAGE_PAGE_SIZE);
        } else {
          setMessages((current) => mergeMessages(current, page));
        }
        setError(null);
      } catch (failure) {
        if (replace) setMessages([]);
        setError(apiErrorMessage(failure));
        return;
      }
      await markRead(conversationId);
    },
    [markRead],
  );

  React.useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  // A deep link that arrives after the page is already open (a second click on
  // an Updates entry) still selects the thread it names.
  React.useEffect(() => {
    if (requested && threads?.some((thread) => thread.id === requested)) {
      setSelected(requested);
    }
  }, [requested, threads]);

  React.useEffect(() => {
    if (!selected) return;
    setMessages([]);
    setHasEarlier(false);
    void loadLatest(selected, true);
  }, [selected, loadLatest]);

  // Back to the tab after a while: whatever arrived in between is fetched.
  React.useEffect(() => {
    const onFocus = () => {
      void loadThreads();
      if (selected) void loadLatest(selected, false);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadThreads, loadLatest, selected]);

  const newestId = messages.length ? messages[messages.length - 1].id : null;
  React.useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (keepOffsetRef.current !== null && scroller) {
      scroller.scrollTop = scroller.scrollHeight - keepOffsetRef.current;
      keepOffsetRef.current = null;
    }
  }, [messages]);
  React.useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [newestId]);

  async function loadEarlier() {
    if (!selected || messages.length === 0) return;
    const oldest = messages[0];
    setLoadingEarlier(true);
    try {
      const page = await listMyMessages(selected, oldest);
      const scroller = scrollerRef.current;
      keepOffsetRef.current = scroller
        ? scroller.scrollHeight - scroller.scrollTop
        : null;
      setMessages((current) => mergeMessages(page, current));
      setHasEarlier(page.length >= MESSAGE_PAGE_SIZE);
    } catch (failure) {
      toast({ title: apiErrorMessage(failure), variant: "destructive" });
    } finally {
      setLoadingEarlier(false);
    }
  }

  async function send() {
    const body = draft.trim();
    const thread = selected;
    if (!body || !thread) return;
    setBusy(true);
    try {
      // The SAME token for every attempt at these words: see ComposerToken.
      const token = composerFor(thread);
      const sent = await sendMyReply(thread, body, token.current());
      token.rotate();
      setMessages((current) => mergeMessages(current, [sent]));
      setDrafts((current) => ({ ...current, [thread]: "" }));
      setThreads((current) =>
        current?.map((row) =>
          row.id === thread ? { ...row, last_message_at: sent.created_at } : row,
        ) ?? current,
      );
    } catch (failure) {
      // The draft AND its token are kept, so "Send" again is a retry of this
      // message, never a second one.
      toast({ title: apiErrorMessage(failure), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  const active = threads?.find((thread) => thread.id === selected) ?? null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Messages</h1>
        <p className="text-sm">
          Conversations with the companies you have applied to.
        </p>
      </div>

      {error ? <InlineError>{error}</InlineError> : null}

      {threads === null ? (
        <LoadingRows rows={3} label="Loading your conversations" />
      ) : threads.length === 0 ? (
        <Card>
          <CardContent className="flex items-center gap-2 py-6">
            <MessagesSquare className="h-4 w-4 shrink-0" aria-hidden="true" />
            <p className="text-sm">
              No conversations yet. A company you have applied to can start one,
              and it will appear here.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
          <nav aria-label="Conversations">
            <ul className="space-y-2">
              {threads.map((thread) => (
                <li key={thread.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(thread.id)}
                    aria-current={thread.id === selected ? "true" : undefined}
                    className={`w-full rounded-md border p-3 text-left text-sm ${
                      thread.id === selected ? "border-navy-600" : ""
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5 font-medium">
                        <Building2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span className="truncate">
                          {thread.company_name ?? "A company"}
                        </span>
                      </span>
                      {thread.unread > 0 ? (
                        <span className="shrink-0 rounded-full bg-brand-600 px-2 py-0.5 text-xs font-semibold text-white">
                          {thread.unread} new
                        </span>
                      ) : null}
                    </span>
                    <span className="block text-xs">
                      {thread.last_message_at
                        ? new Date(thread.last_message_at).toLocaleDateString()
                        : "No messages yet"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="text-base">
                  {active?.company_name ?? "Conversation"}
                </CardTitle>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !selected}
                  onClick={() => selected && void loadLatest(selected, false)}
                >
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="ml-1">Refresh</span>
                </Button>
              </div>
              <CardDescription>{active?.subject ?? ""}</CardDescription>
            </CardHeader>

            <CardContent className="space-y-3">
              <div
                ref={scrollerRef}
                className="max-h-96 space-y-3 overflow-y-auto rounded-md border p-3"
              >
                {hasEarlier ? (
                  <div className="flex justify-center">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={loadingEarlier}
                      onClick={() => void loadEarlier()}
                    >
                      {loadingEarlier ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      <span className="ml-1">Load earlier messages</span>
                    </Button>
                  </div>
                ) : null}
                {messages.length === 0 ? (
                  <p className="text-sm">No messages in this conversation yet.</p>
                ) : null}
                {messages.map((message) => (
                  <article key={message.id} className="space-y-1">
                    <p className="text-xs font-medium">
                      {message.author_party === "candidate"
                        ? PARTY_LABEL.candidate
                        : message.author_name ||
                          PARTY_LABEL[message.author_party] ||
                          "Recruitment team"}
                      <span className="ml-2 font-normal">
                        {new Date(message.created_at).toLocaleString()}
                      </span>
                    </p>
                    <p className="whitespace-pre-wrap text-sm">{message.body}</p>
                  </article>
                ))}
                <div ref={endRef} />
              </div>

              <Textarea
                rows={3}
                value={draft}
                disabled={busy || !selected}
                placeholder="Write a reply"
                aria-label="Reply"
                onChange={(event) => {
                  // Different words are a different message, so they get a
                  // different token. The server refuses one token carrying
                  // two texts rather than quietly keeping the first.
                  if (selected) composerFor(selected).rotate();
                  setDraft(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              <Button
                size="sm"
                disabled={busy || !draft.trim() || !selected}
                onClick={() => void send()}
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Send className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span className="ml-1">Send</span>
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

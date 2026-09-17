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
// connection earns its cost there; a connection on a page somebody opens once
// would be a cost with no reader. The thread loads on open and on demand.

import * as React from "react";
import { Building2, Loader2, MessagesSquare, RefreshCw, Send } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { newClientToken, type Message } from "@/lib/conversations";
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

interface CandidateThread {
  id: string;
  subject: string;
  status: string;
  company_name: string | null;
  last_message_at: string | null;
  inbound: number;
}

const PARTY_LABEL: Record<string, string> = {
  recruiter: "Recruitment team",
  candidate: "You",
  system: "ReadyPick",
};

export default function CandidateMessagesPage() {
  const { toast } = useToast();
  const [threads, setThreads] = React.useState<CandidateThread[] | null>(null);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [draft, setDraft] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const endRef = React.useRef<HTMLDivElement | null>(null);

  const loadThreads = React.useCallback(async () => {
    try {
      const rows = await apiGet<CandidateThread[]>("/conversations/me");
      setThreads(rows);
      setError(null);
      setSelected((current) => current ?? rows[0]?.id ?? null);
    } catch (failure) {
      setThreads([]);
      setError(apiErrorMessage(failure));
    }
  }, []);

  const loadMessages = React.useCallback(async (conversationId: string) => {
    try {
      setMessages(
        await apiGet<Message[]>(`/conversations/me/${conversationId}/messages`),
      );
      setError(null);
    } catch (failure) {
      setMessages([]);
      setError(apiErrorMessage(failure));
    }
  }, []);

  React.useEffect(() => {
    void loadThreads();
  }, [loadThreads]);

  React.useEffect(() => {
    if (selected) void loadMessages(selected);
  }, [selected, loadMessages]);

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length]);

  async function send() {
    const body = draft.trim();
    if (!body || !selected) return;
    setBusy(true);
    try {
      const sent = await apiPost<Message>(`/conversations/me/${selected}/messages`, {
        body,
        client_token: newClientToken(),
      });
      setMessages((current) => [...current, sent]);
      setDraft("");
    } catch (failure) {
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
                    <span className="flex items-center gap-1.5 font-medium">
                      <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
                      {thread.company_name ?? "A company"}
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
                  onClick={() => selected && void loadMessages(selected)}
                >
                  <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="ml-1">Refresh</span>
                </Button>
              </div>
              <CardDescription>{active?.subject ?? ""}</CardDescription>
            </CardHeader>

            <CardContent className="space-y-3">
              <div className="max-h-96 space-y-3 overflow-y-auto rounded-md border p-3">
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
                onChange={(event) => setDraft(event.target.value)}
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

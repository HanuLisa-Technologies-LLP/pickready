"use client";

// The unified assessment conversation.
//
// One question at a time, rendered as a transcript: the asked question on the
// left, the candidate's saved answer on the right. Nothing here shows a score,
// a percentage or a question count out of a total, because the candidate is
// never rated to their face and the client is never shown a number at all.
// `progress_label` is whatever the backend chose to say, and it is displayed
// verbatim.

import * as React from "react";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, Loader2, Send } from "lucide-react";
import { useParams } from "next/navigation";

import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiPost } from "@/lib/api";

interface Conversation {
  conversation_id: string;
  status: "active" | "completed";
  prompt: string | null;
  progress_label: string;
}

interface Exchange {
  prompt: string;
  answer: string;
  /** When each side of the turn appeared, in THIS browser. The transcript is
   *  stored server-side without per-message timestamps, and inventing a
   *  server time here would be a worse lie than showing the local one: these
   *  are the times the candidate actually saw and sent the message. */
  askedAt: Date;
  answeredAt: Date;
}

export default function UnifiedAssessmentPage() {
  const { link_id: linkId } = useParams<{ link_id: string }>();
  const { toast } = useToast();
  const [conversation, setConversation] = React.useState<Conversation | null>(
    null
  );
  const [exchanges, setExchanges] = React.useState<Exchange[]>([]);
  const [answer, setAnswer] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [sending, setSending] = React.useState(false);
  // When the question currently on screen was shown. Captured on arrival so
  // the transcript can record it once the candidate answers.
  const promptShownAt = React.useRef<Date>(new Date());
  const endRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    apiPost<Conversation>(
      `/api/v2/assessments/conversations/links/${linkId}/start`
    )
      .then(setConversation)
      .catch((error) =>
        toast({
          title: "Assessment unavailable",
          description: error instanceof Error ? error.message : undefined,
          variant: "destructive",
        })
      )
      .finally(() => setLoading(false));
  }, [linkId, toast]);

  // Auto-scroll. A conversation that grows off the bottom of the viewport and
  // leaves the reader to find the new message is the single most obvious way a
  // chat stops feeling like one. `behavior: smooth` follows the thread rather
  // than jumping, and it runs on the typing indicator too so the candidate can
  // see that the interviewer is composing.
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [exchanges.length, conversation?.prompt, sending]);

  // A new question is on screen: start its clock.
  React.useEffect(() => {
    if (conversation?.prompt) promptShownAt.current = new Date();
  }, [conversation?.prompt]);

  const respond = async () => {
    if (!conversation?.prompt || !answer.trim()) return;
    const currentPrompt = conversation.prompt;
    const currentAnswer = answer.trim();
    setSending(true);
    try {
      const next = await apiPost<Conversation>(
        `/api/v2/assessments/conversations/${conversation.conversation_id}/respond`,
        { answer: currentAnswer }
      );
      setExchanges((items) => [
        ...items,
        {
          prompt: currentPrompt,
          answer: currentAnswer,
          askedAt: promptShownAt.current,
          answeredAt: new Date(),
        },
      ]);
      setConversation(next);
      setAnswer("");
    } catch (error) {
      toast({
        title: "Could not save your response",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSending(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Your assessment"
        description="Answer in your own words. Each response is saved as you go, so you can close this page and come back."
        actions={
          <Button variant="outline" asChild>
            <Link href="/portal/applications">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Applied Jobs
            </Link>
          </Button>
        }
      />

      <div className="mx-auto max-w-3xl space-y-4">
        {exchanges.map((exchange, index) => (
          <React.Fragment key={index}>
            <Bubble side="asked" at={exchange.askedAt}>
              {exchange.prompt}
            </Bubble>
            <Bubble side="answered" at={exchange.answeredAt}>
              {exchange.answer}
            </Bubble>
          </React.Fragment>
        ))}

        {loading ? (
          <p
            role="status"
            className="flex items-center justify-center gap-2 py-12 text-sm"
          >
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Preparing your assessment
          </p>
        ) : null}

        {/* The interviewer composing. This is not decoration: since the
            assessment became adaptive, the server really is deciding whether
            to follow up on what was just said, and that round trip includes a
            model call. Without this the page simply sits still and the
            candidate cannot tell the difference between thinking and broken. */}
        {sending ? (
          <div className="sm:mr-10" aria-live="polite">
            <p className="sr-only">The interviewer is typing</p>
            <div className="inline-flex items-center gap-1.5 rounded-2xl rounded-tl-md border border-border bg-surface px-5 py-4 shadow-card">
              <Dot delay="0ms" />
              <Dot delay="150ms" />
              <Dot delay="300ms" />
            </div>
          </div>
        ) : null}

        {conversation?.status === "completed" ? (
          <Card className="shadow-card">
            <CardContent className="flex flex-col items-center p-8 text-center">
              <span className="grid h-14 w-14 place-items-center rounded-2xl bg-rating-1-bg text-rating-1">
                <CheckCircle2 className="h-7 w-7" aria-hidden="true" />
              </span>
              <h2 className="mt-4 text-base font-semibold">
                Assessment complete
              </h2>
              <p className="mt-1 max-w-sm text-pretty text-sm leading-6">
                Your responses were saved and your report is being compiled.
              </p>
              <Button className="mt-5" variant="outline" asChild>
                <Link href="/portal/applications">Back to Applied Jobs</Link>
              </Button>
            </CardContent>
          </Card>
        ) : conversation?.prompt ? (
          <>
            <div className="sm:mr-10">
              <div className="rounded-2xl rounded-tl-md border border-border bg-surface p-5 shadow-card">
                <p className="text-xs font-semibold uppercase tracking-[0.12em] text-brand-600">
                  {conversation.progress_label}
                </p>
                <p className="mt-2 text-pretty leading-7">
                  {conversation.prompt}
                </p>
              </div>
            </div>

            <div className="space-y-3 sm:ml-10">
              <label htmlFor="assessment-answer" className="sr-only">
                Your answer
              </label>
              <Textarea
                id="assessment-answer"
                rows={6}
                value={answer}
                disabled={sending}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="Share a specific, honest example."
              />
              <div className="flex justify-end">
                <Button
                  size="lg"
                  disabled={sending || !answer.trim()}
                  onClick={() => void respond()}
                >
                  {sending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Send className="h-4 w-4" aria-hidden="true" />
                  )}
                  {sending ? "Saving" : "Send response"}
                </Button>
              </div>
            </div>
          </>
        ) : null}

        {/* Scroll target. An empty node rather than scrolling the last bubble,
            so the view lands below the newest message instead of pinning its
            top edge to the bottom of the viewport. */}
        <div ref={endRef} aria-hidden="true" />
      </div>
    </div>
  );
}

/** One animated dot of the typing indicator. */
function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-2 w-2 animate-bounce rounded-full bg-brand-600/70"
      style={{ animationDelay: delay }}
    />
  );
}

/** Time of day, e.g. "14:32". Deliberately no date: the whole conversation
 *  happens in one sitting, and a date on every bubble is noise. */
function formatTime(value: Date): string {
    return value.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
}

/**
 * One turn of the transcript. The asked question sits left on the plain card
 * surface; the candidate's own answer sits right in a brand-tinted bubble, so
 * the two are told apart by position and fill rather than by grey text.
 */
function Bubble({
  side,
  at,
  children,
}: {
  side: "asked" | "answered";
  at?: Date;
  children: React.ReactNode;
}) {
  const asked = side === "asked";
  return (
    <div className={asked ? "sm:mr-10" : "sm:ml-10"}>
      <p className="sr-only">{asked ? "Question" : "Your answer"}</p>
      <div
        className={
          asked
            ? "rounded-2xl rounded-tl-md border border-border bg-surface p-5 text-sm leading-7 shadow-card"
            : "rounded-2xl rounded-tr-md border border-brand-600/30 bg-brand-100/70 p-5 text-sm leading-7"
        }
      >
        {children}
      </div>
      {at ? (
        <p
          className={`mt-1 text-[11px] ${asked ? "text-left" : "text-right"}`}
        >
          <time dateTime={at.toISOString()}>{formatTime(at)}</time>
        </p>
      ) : null}
    </div>
  );
}

"use client";

// The unified assessment conversation.
//
// One question at a time, rendered as a transcript: the asked question on the
// left, the candidate's saved answer on the right. Progress is shown as a
// completion percentage and a base-question count; those are navigation, not a
// candidate score. Re-asks and probes deliberately leave both values unchanged.

import * as React from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Pencil,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useParams } from "next/navigation";

import { PageHeader } from "@/components/app-shell";
import {
  AssessmentProgress,
  AssessmentSteps,
} from "@/components/assessment-progress";
import { OptionalProctoringConsent } from "@/components/optional-proctoring-consent";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiPatch, apiPost } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface Conversation {
  conversation_id: string;
  status: "active" | "completed";
  prompt: string | null;
  progress_label: string;
  answered_questions: number;
  total_questions: number;
  is_reask: boolean;
  answer_message_id?: string | null;
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
  messageId?: string;
}

/** The backend rejects an answer over this length with a 422. Enforced here so
 *  a candidate who has just written a long, careful answer is stopped at the
 *  boundary rather than losing it to a validation error after they press Send. */
const MAX_ANSWER = 10000;

/** Show the counter only when it starts to matter. A permanent character count
 *  on an interview answer reads as a word limit and makes people write to it. */
const COUNTER_FROM = MAX_ANSWER - 1000;

/** Where an unsent draft is kept. The page promises "you can close this page
 *  and come back", and that was true of SAVED answers and false of the one
 *  being typed -- a refresh, a tab crash or a phone call lost it. Scoped per
 *  application so two open assessments cannot overwrite each other. */
const draftKey = (linkId: string) => `pickready:assessment-draft:${linkId}`;

export default function UnifiedAssessmentPage() {
  const { link_id: linkId } = useParams<{ link_id: string }>();
  const { toast } = useToast();
  const { user } = useAuth();
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
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);

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

  // Restore an unsent draft. Runs once per application, before the candidate
  // can type, so it cannot clobber something they have already started.
  React.useEffect(() => {
    try {
      const saved = window.localStorage.getItem(draftKey(linkId));
      if (saved) setAnswer(saved);
    } catch {
      // A browser with storage disabled or a full quota. Losing draft
      // persistence is not worth failing the assessment over.
    }
  }, [linkId]);

  // Persist it as they type. Cheap, synchronous and debounced by React's own
  // batching; a candidate mid-sentence when their phone rings keeps their work.
  React.useEffect(() => {
    try {
      if (answer) window.localStorage.setItem(draftKey(linkId), answer);
      else window.localStorage.removeItem(draftKey(linkId));
    } catch {
      /* see above */
    }
  }, [answer, linkId]);

  // Auto-scroll. A conversation that grows off the bottom of the viewport and
  // leaves the reader to find the new message is the single most obvious way a
  // chat stops feeling like one. `behavior: smooth` follows the thread rather
  // than jumping, and it runs on the typing indicator too so the candidate can
  // see that the interviewer is composing.
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [exchanges.length, conversation?.prompt, sending]);

  // A new question is on screen: start its clock, and put the cursor back in
  // the box. Without the refocus the candidate has to reach for the mouse
  // between every one of 45 questions, which is the kind of friction that makes
  // a long assessment feel longer than it is.
  React.useEffect(() => {
    if (!conversation?.prompt) return;
    promptShownAt.current = new Date();
    inputRef.current?.focus();
  }, [conversation?.prompt]);

  /**
   * Send the answer, showing it IMMEDIATELY.
   *
   * WHY OPTIMISTIC, AND WHY IT MATTERS MORE THAN IT USED TO
   * -------------------------------------------------------
   * This request is not a save. Since the assessment became adaptive the server
   * may make up to three model calls on one turn -- classify the answer, decide
   * whether to challenge or probe it, then write the next question -- so it can
   * legitimately take several seconds.
   *
   * The page used to hold the candidate's text in the textarea for that whole
   * time and only move it into the transcript once the response arrived. Every
   * turn therefore ended with the interface visibly doing nothing to the thing
   * the candidate had just acted on, which reads as a dropped click and invites
   * a second press.
   *
   * The answer now moves into the transcript on the press, and the typing
   * indicator explains the wait. On failure the exchange is rolled back and the
   * text is returned to the box, so nothing is ever silently lost -- which is
   * the property that makes optimism safe here rather than merely faster.
   */
  const respond = async () => {
    if (!conversation?.prompt || !answer.trim() || sending) return;
    const currentPrompt = conversation.prompt;
    const currentAnswer = answer.trim();
    const optimistic: Exchange = {
      prompt: currentPrompt,
      answer: currentAnswer,
      askedAt: promptShownAt.current,
      answeredAt: new Date(),
    };

    setSending(true);
    setExchanges((items) => [...items, optimistic]);
    setAnswer("");

    try {
      const next = await apiPost<Conversation>(
        `/api/v2/assessments/conversations/${conversation.conversation_id}/respond`,
        { answer: currentAnswer }
      );
      setExchanges((items) =>
        items.map((item) =>
          item === optimistic
            ? { ...item, messageId: next.answer_message_id ?? undefined }
            : item
        )
      );
      setConversation(next);
    } catch (error) {
      // Roll the turn back completely. Identity comparison rather than an
      // index: nothing else can append while `sending` is true, but a rollback
      // that removed "the last item" would be wrong the moment that changes.
      setExchanges((items) => items.filter((item) => item !== optimistic));
      setAnswer(currentAnswer);
      toast({
        title: "Could not save your response",
        description:
          error instanceof Error
            ? error.message
            : "Your answer is still in the box. Please try sending it again.",
        variant: "destructive",
      });
    } finally {
      setSending(false);
    }
  };

  const saveEditedAnswer = async (index: number, edited: string) => {
    const exchange = exchanges[index];
    if (!conversation || !exchange?.messageId) return;
    await apiPatch(
      `/api/v2/assessments/conversations/${conversation.conversation_id}/answers/${exchange.messageId}`,
      { answer: edited }
    );
    setExchanges((items) =>
      items.map((item, itemIndex) =>
        itemIndex === index ? { ...item, answer: edited } : item
      )
    );
  };

  const candidateInitials = initials(user?.full_name || "You");

  /**
   * Cmd/Ctrl+Enter sends; plain Enter does not.
   *
   * The reverse of an instant-messaging default, and deliberately so: these
   * answers are paragraphs, and a bare Enter that submitted would fire
   * mid-thought on the first line break. The hint under the box says which.
   */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void respond();
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

      <div className="mx-auto max-w-5xl">
        <OptionalProctoringConsent />
        <AssessmentSteps
          answered={conversation?.answered_questions ?? 0}
          total={conversation?.total_questions ?? 45}
        />
        <div className="mt-6 grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <AssessmentProgress
            answered={conversation?.answered_questions ?? 0}
            total={conversation?.total_questions ?? 45}
          />
          <div className="min-w-0 space-y-4">
        {exchanges.map((exchange, index) => (
          <React.Fragment key={index}>
            <Bubble side="asked" at={exchange.askedAt}>
              {exchange.prompt}
            </Bubble>
            <EditableAnswerBubble
              answer={exchange.answer}
              at={exchange.answeredAt}
              initials={candidateInitials}
              canEdit={
                Boolean(exchange.messageId) &&
                index === exchanges.length - 1 &&
                conversation?.status === "active"
              }
              onSave={(edited) => saveEditedAnswer(index, edited)}
            />
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
                <AssessorHeader at={promptShownAt.current} />
                <p className="mt-3 text-xs font-semibold uppercase tracking-[0.12em] text-brand-600">
                  {conversation.is_reask
                    ? `Re-asking ${conversation.progress_label}`
                    : conversation.progress_label}
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
                ref={inputRef}
                id="assessment-answer"
                rows={5}
                value={answer}
                disabled={sending}
                maxLength={MAX_ANSWER}
                onChange={(event) => setAnswer(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Share a specific, honest example."
                // Grows with the answer instead of making a long one scroll
                // inside five fixed rows. Capped so it cannot push the question
                // it is answering off the top of the screen.
                className="max-h-[40vh] min-h-[8rem] resize-y"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs">
                  {/* Stated rather than left to be discovered: a candidate who
                      assumes Enter sends has already lost a paragraph by the
                      time they find out it does not. */}
                  Press Ctrl+Enter (Cmd+Enter on Mac) to send.
                  {answer.length >= COUNTER_FROM ? (
                    <span className="ml-2 font-medium">
                      {MAX_ANSWER - answer.length} characters left
                    </span>
                  ) : null}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={sending || !answer}
                    onClick={() => setAnswer("")}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    Clear
                  </Button>
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
                    {sending ? "Sending" : "Send"}
                  </Button>
                </div>
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
      </div>
    </div>
  );
}

/** One animated dot of the typing indicator.
 *
 * A FADE, NOT A BOUNCE. Tailwind's bounce utility overshoots, and this dot
 * appears while a candidate is waiting to be asked the next question in an
 * assessment of their career. A bouncing indicator on that screen is the
 * product being cheerful at somebody who is nervous. DESIGN.md §7 forbids
 * spring overshoot on anything a person is waiting on, and Impeccable flags it
 * as `bounce-easing`.
 *
 * The comment deliberately does NOT name the utility: Impeccable's detector is
 * a source scan, so an explanation that quotes the banned class makes the
 * detector fire on its own documentation.
 *
 * The animation is defined inline rather than as a Tailwind utility because it
 * exists in exactly one place, and a global `animate-pulse-dot` would be a
 * utility somebody reaches for on a surface where it does not belong.
 */
function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-2 w-2 rounded-full bg-navy-600/70 motion-safe:animate-[typing-dot_1.2s_ease-in-out_infinite]"
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

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] || "Y") + (parts[1]?.[0] || "");
}

function AssessorHeader({ at }: { at: Date }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-100 text-brand-700">
        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
      <span className="font-semibold">AI Assessor</span>
      <time className="ml-auto" dateTime={at.toISOString()}>
        {formatTime(at)}
      </time>
    </div>
  );
}

function EditableAnswerBubble({
  answer,
  at,
  initials: badge,
  canEdit,
  onSave,
}: {
  answer: string;
  at: Date;
  initials: string;
  canEdit: boolean;
  onSave: (edited: string) => Promise<void>;
}) {
  const { toast } = useToast();
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(answer);
  const [saving, setSaving] = React.useState(false);

  const save = async () => {
    if (!draft.trim() || draft.trim() === answer) {
      setEditing(false);
      setDraft(answer);
      return;
    }
    setSaving(true);
    try {
      await onSave(draft.trim());
      setEditing(false);
    } catch (error) {
      toast({
        title: "Could not edit your response",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="sm:ml-10">
      <div className="rounded-2xl rounded-tr-md border border-brand-600/30 bg-brand-100/70 p-5 text-sm leading-7">
        <div className="mb-3 flex items-center gap-2 text-xs">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-600 font-semibold text-white">
            {badge.toUpperCase()}
          </span>
          <span className="font-semibold">You</span>
          <time className="ml-auto" dateTime={at.toISOString()}>
            {formatTime(at)}
          </time>
          {canEdit && !editing ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setEditing(true)}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
              Edit
            </Button>
          ) : null}
        </div>
        {editing ? (
          <div className="space-y-2">
            <Textarea
              value={draft}
              maxLength={MAX_ANSWER}
              onChange={(event) => setDraft(event.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={saving}
                onClick={() => {
                  setDraft(answer);
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={saving || !draft.trim()}
                onClick={() => void save()}
              >
                {saving ? "Saving" : "Save edit"}
              </Button>
            </div>
          </div>
        ) : (
          <p className="whitespace-pre-wrap">{answer}</p>
        )}
      </div>
    </div>
  );
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
        {asked && at ? <AssessorHeader at={at} /> : null}
        {asked && at ? <div className="h-3" aria-hidden="true" /> : null}
        {children}
      </div>
      {at && !asked ? (
        <p
          className={`mt-1 text-[11px] ${asked ? "text-left" : "text-right"}`}
        >
          <time dateTime={at.toISOString()}>{formatTime(at)}</time>
        </p>
      ) : null}
    </div>
  );
}

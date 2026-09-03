"use client";

// The unified assessment conversation.
//
// One question at a time, rendered as a transcript: the asked question on the
// left, the candidate's saved answer on the right. Progress is shown as a
// completion percentage and a base-question count; those are navigation, not a
// candidate score. Re-asks and probes deliberately leave both values unchanged.
//
// SIX FORMATS, ONE PLAYER. The question on screen carries a type, and the
// answer field is whatever `QuestionRenderer` dispatches for it. The player
// itself knows only two kinds of turn: PROSE (an evidence-based or
// short-answer question, and every follow-up or re-ask, which the server
// always words as a question) goes up as `answer`; everything else goes up as
// `answer_payload` in the shape the server validates for the type. The
// navigation is unchanged: linear, one question at a time, no going back,
// because per-question timing and the proctoring baseline both assume it
// (assessment spec 5.3).
//
// Rendered INSIDE `components/proctoring/proctoring-shell`, which owns consent,
// the system check and the monitoring session and hands this component the
// `ProctoringBridge` from lib/assessment/contracts.ts through
// `useProctoring()`. Proctoring is mandatory: this component has no unmonitored
// mode and the page never mounts it outside the shell. Every answer field is
// wired to the bridge's hooks for the turn it answers, and every submission
// carries what those hooks recorded plus the time a warning held the screen.

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

import { PageHeader } from "@/components/app-shell";
import {
  AssessmentProgress,
  AssessmentSteps,
} from "@/components/assessment-progress";
import { AutosaveIndicator } from "@/components/assessment/autosave-indicator";
import { QuestionRenderer } from "@/components/assessment/question-renderer";
import { MAX_ANSWER } from "@/components/assessment/text-answer-field";
import { useProctoring } from "@/components/proctoring/proctoring-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiPatch, apiPost } from "@/lib/api";
import {
  answerLine,
  emptyAnswerFor,
  isAnswerComplete,
  isAnswerEmpty,
  textOf,
  turnIsProse,
  turnKeyFor,
} from "@/lib/assessment/answers";
import { clearDraft, readDraft, useAutosaveDraft } from "@/lib/assessment/autosave";
import type {
  AnswerBehaviour,
  AnswerPayload,
  ConversationTurn,
  ProctoringFieldHooks,
  QuestionOut,
  RespondBody,
} from "@/lib/assessment/contracts";
import { timeAllocationPhrase } from "@/lib/assessment/time-guidance";
import { useAuth } from "@/lib/auth-context";

interface Exchange {
  prompt: string;
  /** The readable line: the prose itself, or the server's rendering of a
   *  structured answer once it arrives. */
  answer: string;
  /** True when the answer was prose and can therefore still be edited while
   *  it is the latest one. A structured answer was scored on submission and
   *  is not re-opened. */
  prose: boolean;
  /** When each side of the turn appeared, in THIS browser. The transcript is
   *  stored server-side without per-message timestamps, and inventing a
   *  server time here would be a worse lie than showing the local one: these
   *  are the times the candidate actually saw and sent the message. */
  askedAt: Date;
  answeredAt: Date;
  messageId?: string;
}

/** What was consumed from the bridge for a turn whose send failed. The
 *  bridge clears its counters on read, so a retry after a network blip would
 *  otherwise report zero paused time and no behaviour for a turn that had
 *  both. Kept until the turn is accepted. */
interface CarriedCapture {
  turnKey: string;
  pausedMs: number;
  behaviour: AnswerBehaviour | null;
}

export function AssessmentConversation({ linkId }: { linkId: string }) {
  const { toast } = useToast();
  const { user } = useAuth();
  const bridge = useProctoring();
  const [conversation, setConversation] = React.useState<ConversationTurn | null>(null);
  const [exchanges, setExchanges] = React.useState<Exchange[]>([]);
  const [value, setValue] = React.useState<AnswerPayload | null>(null);
  const [fieldHooks, setFieldHooks] = React.useState<ProctoringFieldHooks | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [sending, setSending] = React.useState(false);
  // When the question currently on screen was shown. Captured on arrival so
  // the transcript can record it once the candidate answers.
  const promptShownAt = React.useRef<Date>(new Date());
  const endRef = React.useRef<HTMLDivElement | null>(null);
  const carried = React.useRef<CarriedCapture | null>(null);
  const endedFor = React.useRef<string | null>(null);
  // The bridge is read through a ref inside effects so a re-render of the
  // shell that hands down a new bridge object does not restart a capture:
  // `fieldHooksFor` starts a fresh capture for its key every time it is
  // called, and it must be called exactly once per turn.
  const bridgeRef = React.useRef(bridge);
  bridgeRef.current = bridge;

  const question: QuestionOut | null = conversation?.question ?? null;
  const active = conversation?.status === "active" && Boolean(conversation.prompt);
  const turnKey = active
    ? turnKeyFor(question, conversation.answered_questions, conversation.is_reask)
    : null;
  const prose = turnIsProse(question);
  const starterCode =
    question?.question_type === "coding"
      ? String((question.payload as { starter_code?: string }).starter_code ?? "")
      : "";
  const isEmpty = React.useCallback(
    (candidate: AnswerPayload | null) => isAnswerEmpty(candidate, starterCode),
    [starterCode]
  );
  const autosave = useAutosaveDraft(linkId, turnKey, value, isEmpty);

  React.useEffect(() => {
    apiPost<ConversationTurn>(
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

  // A new turn is on screen: restore its draft (before the candidate can
  // type, so it cannot clobber something they have already started), start
  // its clock, and open its behaviour capture. All three are keyed by the
  // turn, so a re-ask of the same base question is a fresh capture and a
  // fresh draft, which is what the server measures it as.
  React.useEffect(() => {
    if (turnKey === null) {
      setValue(null);
      setFieldHooks(null);
      return;
    }
    const restored = readDraft(linkId, turnKey);
    setValue(restored ?? (question ? emptyAnswerFor(question) : { text: "" }));
    promptShownAt.current = new Date();
    setFieldHooks(bridgeRef.current.fieldHooksFor(turnKey));
    // `question` is the object the key was derived from; a new key always
    // means a new question object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkId, turnKey]);

  // The conversation has ended, one way or the other: tell the shell once so
  // monitoring stops and the camera is released. Keyed so a re-render with
  // the same terminal state cannot say it twice.
  React.useEffect(() => {
    if (!conversation) return;
    if (conversation.status !== "completed" && conversation.status !== "terminated") return;
    const key = `${conversation.conversation_id}:${conversation.status}`;
    if (endedFor.current === key) return;
    endedFor.current = key;
    bridgeRef.current.onConversationEnded(conversation.status);
  }, [conversation]);

  // Auto-scroll. A conversation that grows off the bottom of the viewport and
  // leaves the reader to find the new message is the single most obvious way a
  // chat stops feeling like one. `behavior: smooth` follows the thread rather
  // than jumping, and it runs on the typing indicator too so the candidate can
  // see that the interviewer is composing.
  React.useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [exchanges.length, conversation?.prompt, sending]);

  const complete = isAnswerComplete(value, starterCode);

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
   * answer is returned to the field, so nothing is ever silently lost -- which
   * is the property that makes optimism safe here rather than merely faster.
   */
  const respond = async () => {
    if (!conversation?.prompt || turnKey === null || value === null || !complete || sending) {
      return;
    }
    const currentPrompt = conversation.prompt;
    const currentValue = value;
    const currentQuestion = question;
    const currentKey = turnKey;
    const optimistic: Exchange = {
      prompt: currentPrompt,
      answer: answerLine(currentQuestion, currentValue),
      prose,
      askedAt: promptShownAt.current,
      answeredAt: new Date(),
    };

    // What the bridge recorded for this turn, plus anything a failed earlier
    // attempt at the same turn had already consumed.
    const previous = carried.current?.turnKey === currentKey ? carried.current : null;
    const pausedMs = (previous?.pausedMs ?? 0) + bridgeRef.current.consumePausedMs();
    const behaviour =
      bridgeRef.current.collectAnswerBehaviour(currentKey) ?? previous?.behaviour ?? null;
    carried.current = { turnKey: currentKey, pausedMs, behaviour };

    const body: RespondBody = prose
      ? { answer: textOf(currentValue).trim(), paused_ms: pausedMs }
      : { answer: "", answer_payload: currentValue, paused_ms: pausedMs };
    if (behaviour) body.behaviour = behaviour;

    setSending(true);
    setExchanges((items) => [...items, optimistic]);
    setValue(currentQuestion ? emptyAnswerFor(currentQuestion) : { text: "" });

    try {
      const next = await apiPost<ConversationTurn>(
        `/api/v2/assessments/conversations/${conversation.conversation_id}/respond`,
        body
      );
      carried.current = null;
      clearDraft(linkId, currentKey);
      setExchanges((items) =>
        items.map((item) =>
          item === optimistic
            ? {
                ...item,
                messageId: next.answer_message_id ?? undefined,
                // The server's line is the one the recruiter's transcript
                // shows; adopt it so the candidate and the recruiter read the
                // same words for the same answer.
                answer: next.answer_line?.trim() || item.answer,
              }
            : item
        )
      );
      setConversation(next);
    } catch (error) {
      // Roll the turn back completely. Identity comparison rather than an
      // index: nothing else can append while `sending` is true, but a rollback
      // that removed "the last item" would be wrong the moment that changes.
      setExchanges((items) => items.filter((item) => item !== optimistic));
      setValue(currentValue);
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
                    exchange.prose &&
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

            {conversation?.status === "terminated" ? (
              <Card className="shadow-card" data-testid="termination-notice">
                <CardContent className="p-8 text-center">
                  <h2 className="text-base font-semibold">This assessment has ended</h2>
                  <p className="mt-2 max-w-md text-pretty text-sm leading-6 sm:mx-auto">
                    {conversation.termination_message ??
                      "The assessment was ended before its final question. The answers you had already sent were kept."}
                  </p>
                  <Button className="mt-5" variant="outline" asChild>
                    <Link href="/portal/applications">Back to Applied Jobs</Link>
                  </Button>
                </CardContent>
              </Card>
            ) : conversation?.status === "completed" ? (
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
            ) : conversation?.prompt && turnKey !== null ? (
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
                    {question ? (
                      // Guidance, not a limit: nothing stops the candidate at
                      // the mark, and the wording says "about" so it cannot
                      // read as a clock.
                      <p className="mt-3 text-xs" data-testid="time-guidance">
                        Suggested time: {timeAllocationPhrase(question.time_allocation_seconds)}.
                      </p>
                    ) : null}
                  </div>
                </div>

                <div className="space-y-3 sm:ml-10">
                  {fieldHooks && value !== null ? (
                    <QuestionRenderer
                      // A follow-up or a re-ask has no question row of its
                      // own; it is prose, and prose renders as a short answer.
                      question={question ?? proseTurn(turnKey)}
                      prompt={conversation.prompt}
                      value={value}
                      onChange={setValue}
                      disabled={sending}
                      autosave={autosave}
                      fieldHooks={fieldHooks}
                      onSubmitShortcut={() => void respond()}
                    />
                  ) : null}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <AutosaveIndicator state={autosave} />
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        disabled={sending || isEmpty(value)}
                        onClick={() =>
                          setValue(question ? emptyAnswerFor(question) : { text: "" })
                        }
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                        Clear
                      </Button>
                      <Button
                        size="lg"
                        disabled={sending || !complete}
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

/** The question shape a prose follow-up or re-ask renders through. It has no
 *  row on the server, so its id is the turn key and its allocation is not
 *  shown (the guidance line is drawn only for a real question). */
function proseTurn(turnKey: string): QuestionOut {
  return {
    id: turnKey,
    question_type: "short_answer",
    payload: {},
    time_allocation_seconds: 0,
  };
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
          <p className="whitespace-pre-wrap" data-testid="answer-bubble">
            {answer}
          </p>
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

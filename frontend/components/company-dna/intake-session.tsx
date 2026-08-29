"use client";

// The Company DNA intake session: twelve sections, one at a time.
//
// FOUR THINGS THIS SCREEN HAS TO GET RIGHT
// ----------------------------------------
//  * NO DEAD ENDS. Every state has a next action. A refused answer keeps the
//    section open with the reason under the field; a section with nothing left
//    to answer offers the next one; the last section offers the read-back.
//  * SAVE AND RESUME. Every accepted answer is written to the server as it is
//    given, so closing the tab costs nothing. There is no local draft to go
//    stale against the row.
//  * WHY WE ARE ASKING, per section, in the terms a CHRO cares about. A
//    ninety-minute instrument that does not say why is a form people guess at.
//  * THE READ-BACK IS THE LAST STEP. Bodha states its compiled understanding in
//    plain language and the session cannot close until somebody confirms THAT
//    understanding: the confirmation carries the fingerprint the server
//    recompiles and compares, so an answer changed afterwards invalidates it.

import * as React from "react";

import { Check, ChevronLeft, ChevronRight, CircleDot } from "lucide-react";

import { ApiError, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Section as Card } from "@/components/page-primitives";

import { QuestionField } from "./question-field";
import { UnderstandingReadback } from "./understanding-readback";
import type { AnswerRefusal, IntakeSession, Question } from "./types";
import { companyDnaPath } from "./types";

/**
 * Bodha's refusal, out of a 422 body, or null when this was some other failure.
 *
 * The two are kept apart deliberately. A refusal is a message for the client
 * about the answer they just gave and belongs under that field; a transport
 * failure is a message about the system and belongs where system messages go.
 * Rendering one as the other is how "the network dropped" ends up reading as
 * "your answer was wrong".
 */
export function refusalFrom(error: unknown): AnswerRefusal | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const body = error.detail;
  if (!body || typeof body !== "object") return null;
  const inner = (body as { detail?: unknown }).detail;
  if (
    inner &&
    typeof inner === "object" &&
    typeof (inner as AnswerRefusal).message === "string" &&
    typeof (inner as AnswerRefusal).question_key === "string"
  ) {
    return inner as AnswerRefusal;
  }
  return null;
}

export function IntakeSessionSurface({
  clientId,
  session,
  onSession,
  onCompleted,
}: {
  clientId: string;
  session: IntakeSession;
  onSession: (next: IntakeSession) => void;
  /** Called once the version is frozen, so the page can move on to the
   *  completed view. Without it, confirming would leave the client looking at
   *  a session that no longer exists, which is the dead end this screen is
   *  supposed to have none of. */
  onCompleted: () => void;
}) {
  const [index, setIndex] = React.useState(() => {
    const outstanding = session.sections.findIndex((s) => !s.complete);
    return outstanding === -1 ? session.sections.length : outstanding;
  });
  const [refusals, setRefusals] = React.useState<Record<string, string>>({});
  const [pending, setPending] = React.useState<string | null>(null);
  const [transportError, setTransportError] = React.useState<string | null>(null);

  const onReadback = index >= session.sections.length;
  const section = onReadback ? null : session.sections[index];

  const submit = React.useCallback(
    async (question: Question, answer: unknown) => {
      setPending(question.key);
      setTransportError(null);
      try {
        const next = await apiPost<IntakeSession>(
          `${companyDnaPath(clientId)}/${session.id}/messages`,
          { question_key: question.key, answer }
        );
        setRefusals((current) => {
          const { [question.key]: _removed, ...rest } = current;
          return rest;
        });
        onSession(next);
      } catch (error) {
        const refused = refusalFrom(error);
        if (refused) {
          // Bodha's own words, under the field that caused them. A refusal in a
          // toast is a refusal the client reads after they have already
          // scrolled past the question it was about.
          setRefusals((current) => ({
            ...current,
            [refused.question_key]: refused.message,
          }));
        } else {
          setTransportError(
            error instanceof Error
              ? error.message
              : "That answer could not be saved. Try again."
          );
        }
      } finally {
        setPending(null);
      }
    },
    [clientId, onSession, session.id]
  );

  return (
    <div className="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
      <SectionRail
        sections={session.sections}
        index={index}
        readbackReady={session.ready_to_complete}
        onSelect={setIndex}
      />

      <div className="min-w-0">
        {onReadback ? (
          <UnderstandingReadback
            clientId={clientId}
            session={session}
            onRevisit={() => setIndex(session.sections.length - 1)}
            onCompleted={onCompleted}
          />
        ) : section ? (
          <Card
            title={section.title}
            description={section.intent}
            contentClassName="divide-y divide-border"
          >
            {section.questions.map((question) => (
              <QuestionField
                key={question.key}
                question={question}
                section={section}
                value={session.answers[question.key]}
                refusal={refusals[question.key] ?? null}
                disabled={pending === question.key}
                onChange={(answer) => void submit(question, answer)}
              />
            ))}
          </Card>
        ) : null}

        {transportError ? (
          <p role="alert" className="mt-4 text-sm font-medium">
            {transportError}
          </p>
        ) : null}

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <Button
            variant="outline"
            disabled={index === 0}
            onClick={() => setIndex((current) => Math.max(0, current - 1))}
          >
            <ChevronLeft className="mr-1 h-4 w-4" aria-hidden="true" />
            Previous section
          </Button>
          <p className="text-sm">
            Everything you answer is saved as you go. You can close this and come
            back to it.
          </p>
          {onReadback ? null : (
            <Button
              onClick={() =>
                setIndex((current) =>
                  Math.min(session.sections.length, current + 1)
                )
              }
            >
              {index === session.sections.length - 1
                ? "Review what we understood"
                : "Next section"}
              <ChevronRight className="ml-1 h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The twelve sections, with what is outstanding in each.
 *
 * A tick means nothing is outstanding, which for the four sections that carry
 * no required question is true from the start. Those are labelled optional
 * rather than ticked, because a tick nobody earned reads as progress and hides
 * a section somebody might want to answer.
 */
function SectionRail({
  sections,
  index,
  readbackReady,
  onSelect,
}: {
  sections: IntakeSession["sections"];
  index: number;
  readbackReady: boolean;
  onSelect: (next: number) => void;
}) {
  return (
    <nav aria-label="Intake sections" className="lg:sticky lg:top-6 lg:self-start">
      <ol className="space-y-1">
        {sections.map((section, position) => {
          const active = position === index;
          const optional = section.required_total === 0;
          return (
            <li key={section.key}>
              <button
                type="button"
                aria-current={active ? "step" : undefined}
                onClick={() => onSelect(position)}
                className={cn(
                  "flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left text-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400",
                  active ? "bg-navy-50 font-semibold" : "hover:bg-navy-50"
                )}
              >
                <span className="mt-0.5 shrink-0" aria-hidden="true">
                  {section.complete && !optional ? (
                    <Check className="h-4 w-4 text-teal-700" />
                  ) : (
                    <CircleDot className="h-4 w-4" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="block">{section.title}</span>
                  <span className="block text-xs">
                    {optional
                      ? "Optional"
                      : section.complete
                        ? "Answered"
                        : "Not answered yet"}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        <li>
          <button
            type="button"
            aria-current={index >= sections.length ? "step" : undefined}
            onClick={() => onSelect(sections.length)}
            className={cn(
              "flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400",
              index >= sections.length ? "bg-navy-50 font-semibold" : "hover:bg-navy-50"
            )}
          >
            <span className="mt-0.5 shrink-0" aria-hidden="true">
              {readbackReady ? (
                <Check className="h-4 w-4 text-teal-700" />
              ) : (
                <CircleDot className="h-4 w-4" />
              )}
            </span>
            <span className="min-w-0">
              <span className="block">What we understood</span>
              <span className="block text-xs">
                {readbackReady ? "Ready for you to confirm" : "After the sections"}
              </span>
            </span>
          </button>
        </li>
      </ol>
    </nav>
  );
}

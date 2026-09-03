"use client";

// What a candidate was actually asked, and what they actually answered.
//
// WHY THIS SCREEN EXISTS
// ----------------------
// A PPI Assessment Report states a grade. This is the evidence behind it, and
// until 2026-08-06 there was no way for a recruiter to see it at all: the
// transcript lived in `assessment_messages` and the only reader was a psql
// session. A recruiter deciding whether to interview someone, and a candidate
// disputing a grade, both need the answers themselves.
//
// It matters more now than it did. Technical questions used to be a preset bank
// a company authored, so "what was asked" was knowable by opening the job. They
// are now written per candidate during the conversation, which is a better
// interview and an unreadable one without this.
//
// SIX FORMATS, ONE VIEW (assessment spec 7). Every exchange still shows the
// question as the candidate saw it and the answer as they gave it. What the
// format adds sits BELOW that, from `detail`: an MCQ's options with the chosen
// and the correct ones marked in words, a fill-blank's inputs beside what was
// accepted, a coding answer syntax-highlighted with how it was read and the
// note that it was not run, and, for an evidence question, the resume item
// that prompted it, which is the most valuable thing on this screen: it is
// what was being probed.
//
// WHAT IS DELIBERATELY NOT HERE
// -----------------------------
// No score, no rubric, no required level, no number of any kind. The standing
// rule covers this screen exactly as it covers the report: rated output is one
// of four words, correctness is a word, time spent is a phrase, and the rubric
// is internal scoring machinery. Nothing here re-words or summarises an answer
// either -- a summary of an answer is not evidence of what someone said.

import * as React from "react";
import { Loader2, MessageSquare, User } from "lucide-react";

import { CodeEditor } from "@/components/assessment/code-editor";
import { apiGet } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BLANK_MARKER, languageLabel } from "@/lib/assessment/answers";
import type { QuestionType } from "@/lib/assessment/contracts";
import { numberInWords } from "@/lib/assessment/words";

/** Mirrors `schemas/assessments.TranscriptAnswerDetailOut`. */
export interface TranscriptAnswerDetail {
  /** The candidate view of the payload: options in the order the candidate
   *  saw them, the fill-blank template, the coding language. */
  payload: Record<string, unknown>;
  /** The answer as submitted, in its type's shape. */
  answer: Record<string, unknown>;
  /** For an MCQ, `correct_option_ids`; for a fill-blank, `accepted`, one list
   *  of accepted answers per blank in blank order. */
  answer_key: Record<string, unknown>;
  /** correct | partially_correct | incorrect | not_answered, or null for a
   *  format that has no correctness. A WORD, never a score. */
  correctness: string | null;
  /** Per blank: exact | equivalent | incorrect | not_answered. */
  blank_results: string[];
  evaluation_reasoning: string | null;
  evaluation_citations: string[];
  not_executed_note: string | null;
  /** A phrase ("about two minutes"), never a count. */
  time_spent: string | null;
}

export interface TranscriptExchange {
  ordinal: number;
  domain: string;
  question: string;
  answer: string;
  criterion: string | null;
  follow_up: boolean;
  asked_at: string | null;
  question_type?: QuestionType | null;
  /** The resume item an evidence question was generated from. */
  resume_anchor?: string | null;
  detail?: TranscriptAnswerDetail | null;
}

export interface Transcript {
  job_candidate_link_id: string;
  candidate_name: string | null;
  job_title: string | null;
  status: string;
  completed_at: string | null;
  exchanges: TranscriptExchange[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One page. The endpoint caps at 200, which comfortably holds the longest
 * interview the product can produce (45 base questions plus up to 15 probes),
 * so the common case is a single fetch and the Load-more control never appears.
 * It exists for the case that is not common yet.
 */
const PAGE = 100;

const STATUS_COPY: Record<string, string> = {
  not_started: "This candidate has not opened their assessment yet.",
  active: "This assessment is still in progress.",
  completed: "Assessment complete.",
  terminated: "This assessment was ended before its final question.",
};

/** The format, for the reader. Words a recruiter uses, not the type ids. */
export const FORMAT_LABELS: Record<QuestionType, string> = {
  evidence_based: "Evidence-based",
  short_answer: "Short answer",
  mcq_single: "Multiple choice",
  mcq_multi: "Multiple choice, more than one answer",
  fill_blank: "Fill in the blank",
  coding: "Coding",
};

/** Correctness, as the sentence a recruiter reads. */
export const CORRECTNESS_COPY: Record<string, string> = {
  correct: "Correct",
  partially_correct: "Partially correct",
  incorrect: "Not correct",
  not_answered: "Not answered",
};

const BLANK_RESULT_COPY: Record<string, string> = {
  exact: "Exact match",
  equivalent: "Accepted as equivalent",
  incorrect: "Not accepted",
  not_answered: "Left blank",
};

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The correct option ids from an MCQ answer key. A single-answer question's
 *  key names one id; it is read as a list of one so both formats render
 *  through the same marking. */
export function correctOptionIds(answerKey: Record<string, unknown>): string[] {
  const many = asStringList(answerKey.correct_option_ids);
  if (many.length > 0) return many;
  return typeof answerKey.correct_option_id === "string" ? [answerKey.correct_option_id] : [];
}

/** The accepted answers per blank, in blank order. */
export function acceptedPerBlank(answerKey: Record<string, unknown>, blankCount: number): string[][] {
  const accepted = Array.isArray(answerKey.accepted) ? answerKey.accepted : [];
  return Array.from({ length: blankCount }, (_, index) => asStringList(accepted[index]));
}

function McqDetail({ detail }: { detail: TranscriptAnswerDetail }) {
  const options = Array.isArray(detail.payload.options)
    ? (detail.payload.options as { id: string; text: string }[])
    : [];
  const chosen = new Set<string>([
    ...(typeof detail.answer.selected_option_id === "string" ? [detail.answer.selected_option_id] : []),
    ...asStringList(detail.answer.selected_option_ids),
  ]);
  const correct = new Set(correctOptionIds(detail.answer_key));

  return (
    <div className="mt-3 space-y-2" data-testid="mcq-detail">
      {detail.correctness ? (
        <p className="text-sm font-semibold">
          {CORRECTNESS_COPY[detail.correctness] ?? detail.correctness}
        </p>
      ) : null}
      <ul className="space-y-1.5">
        {options.map((option) => {
          const wasChosen = chosen.has(option.id);
          const isCorrect = correct.has(option.id);
          const mark = wasChosen
            ? isCorrect
              ? "Chosen, and correct"
              : "Chosen, not correct"
            : isCorrect
              ? "Correct answer, not chosen"
              : null;
          return (
            <li
              key={option.id}
              className={[
                "flex flex-wrap items-start justify-between gap-2 border p-2.5 text-sm",
                // Navy marks what the candidate did; teal marks what the
                // evidence says. The word beside it carries the meaning.
                wasChosen ? "border-navy-600" : "border-border",
                isCorrect ? "bg-teal-50" : "bg-surface",
              ].join(" ")}
            >
              <span>{option.text}</span>
              {mark ? <span className="text-xs font-semibold">{mark}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function FillBlankDetail({ detail }: { detail: TranscriptAnswerDetail }) {
  const template = typeof detail.payload.template === "string" ? detail.payload.template : "";
  const values = asStringList(detail.answer.values);
  const segments = template.split(BLANK_MARKER);
  const blankCount = Math.max(segments.length - 1, values.length);
  const accepted = acceptedPerBlank(detail.answer_key, blankCount);

  return (
    <div className="mt-3 space-y-3" data-testid="fill-blank-detail">
      {detail.correctness ? (
        <p className="text-sm font-semibold">
          {CORRECTNESS_COPY[detail.correctness] ?? detail.correctness}
        </p>
      ) : null}
      <p className="whitespace-pre-wrap text-sm leading-7">
        {segments.map((segment, index) => (
          <React.Fragment key={index}>
            {segment}
            {index < segments.length - 1 ? (
              <span className="mx-1 border-b-2 border-navy-600 px-1 font-medium">
                {(values[index] ?? "").trim() || BLANK_MARKER}
              </span>
            ) : null}
          </React.Fragment>
        ))}
      </p>
      <ul className="space-y-1.5 text-sm">
        {Array.from({ length: blankCount }, (_, index) => (
          <li key={index} className="border border-border bg-surface p-2.5">
            <span className="font-semibold">Blank {numberInWords(index + 1)}: </span>
            <span>
              typed {(values[index] ?? "").trim() ? `"${values[index].trim()}"` : "nothing"}
            </span>
            {accepted[index].length > 0 ? (
              <span>; accepted: {accepted[index].join(", ")}</span>
            ) : null}
            {detail.blank_results[index] ? (
              <span className="ml-2 text-xs font-semibold">
                {BLANK_RESULT_COPY[detail.blank_results[index]] ?? detail.blank_results[index]}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reasoning({ detail }: { detail: TranscriptAnswerDetail }) {
  if (!detail.evaluation_reasoning && detail.evaluation_citations.length === 0) return null;
  return (
    <div className="mt-3 space-y-2" data-testid="evaluation-reasoning">
      <p className="text-xs font-semibold uppercase tracking-wide">How this answer was read</p>
      {detail.evaluation_reasoning ? (
        <p className="whitespace-pre-wrap text-sm">{detail.evaluation_reasoning}</p>
      ) : null}
      {detail.evaluation_citations.length > 0 ? (
        // Teal: these are the words the reading rests on.
        <ul className="space-y-1 border-l border-teal-600 pl-3 text-sm">
          {detail.evaluation_citations.map((citation, index) => (
            <li key={index} className="whitespace-pre-wrap">
              {citation}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function CodingDetail({ detail }: { detail: TranscriptAnswerDetail }) {
  const language =
    typeof detail.answer.language === "string"
      ? detail.answer.language
      : typeof detail.payload.language === "string"
        ? detail.payload.language
        : "plaintext";
  const code = typeof detail.answer.code === "string" ? detail.answer.code : "";

  return (
    <div className="mt-3 space-y-3" data-testid="coding-detail">
      <p className="text-xs font-medium">{languageLabel(language)}</p>
      <CodeEditor
        value={code}
        language={language}
        readOnly
        ariaLabel="The code as submitted"
        className="[&_.cm-editor]:min-h-[6rem]"
      />
      {detail.not_executed_note ? (
        <p className="border border-border bg-muted p-3 text-sm" data-testid="not-executed-note">
          <span className="font-semibold">Not executed. </span>
          {detail.not_executed_note}
        </p>
      ) : null}
      <Reasoning detail={detail} />
    </div>
  );
}

function ProbeAnchor({ anchor }: { anchor: string }) {
  return (
    // Teal fill: the anchor is the piece of the candidate's own resume this
    // question exists to test, which is exactly what teal means here.
    <div className="mb-3 border border-teal-600/40 bg-teal-50 p-3" data-testid="resume-anchor">
      <p className="text-xs font-semibold uppercase tracking-wide">What was being probed</p>
      <p className="mt-1 whitespace-pre-wrap text-sm">{anchor}</p>
    </div>
  );
}

function FormatDetail({ exchange }: { exchange: TranscriptExchange }) {
  const detail = exchange.detail;
  if (!detail) return null;
  switch (exchange.question_type) {
    case "mcq_single":
    case "mcq_multi":
      return <McqDetail detail={detail} />;
    case "fill_blank":
      return <FillBlankDetail detail={detail} />;
    case "coding":
      return <CodingDetail detail={detail} />;
    default:
      return <Reasoning detail={detail} />;
  }
}

function Exchange({ exchange }: { exchange: TranscriptExchange }) {
  const structured =
    exchange.question_type === "mcq_single" ||
    exchange.question_type === "mcq_multi" ||
    exchange.question_type === "fill_blank" ||
    exchange.question_type === "coding";
  return (
    <li className="rounded-lg border p-4" data-question-type={exchange.question_type ?? undefined}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide">
          {exchange.follow_up ? "Follow-up" : `Question ${exchange.ordinal}`}
        </span>
        {exchange.criterion ? (
          // The skill or competency this answer was filed under. This is what
          // makes a transcript readable as evidence rather than as a wall of
          // text: it is the criterion the answer was graded against.
          <Badge variant="outline">{exchange.criterion}</Badge>
        ) : null}
        {exchange.question_type ? (
          <Badge variant="muted">{FORMAT_LABELS[exchange.question_type]}</Badge>
        ) : null}
        {exchange.detail?.time_spent ? (
          <span className="ml-auto text-xs" data-testid="time-spent">
            Time spent: {exchange.detail.time_spent}
          </span>
        ) : null}
      </div>

      {exchange.resume_anchor ? <ProbeAnchor anchor={exchange.resume_anchor} /> : null}

      <div className="flex gap-2.5">
        <MessageSquare className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p className="text-sm font-medium">{exchange.question}</p>
      </div>

      <div className="mt-3 border-t pt-3">
        <div className="flex gap-2.5">
          <User className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {/* `whitespace-pre-wrap` because a candidate's paragraph breaks are
              part of what they wrote. Collapsing them would be an edit. For a
              structured format this is the server's readable line; the
              structure itself is drawn beneath it. */}
          <p className="whitespace-pre-wrap text-sm">
            {structured && exchange.detail ? (
              <span className="sr-only">{exchange.answer}</span>
            ) : (
              exchange.answer
            )}
          </p>
        </div>
        <FormatDetail exchange={exchange} />
      </div>
    </li>
  );
}

export function AssessmentTranscriptModal({
  open,
  onOpenChange,
  linkId,
  candidateName,
  jobTitle,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  linkId: string | null;
  candidateName: string;
  jobTitle?: string | null;
}) {
  const { toast } = useToast();
  const [transcript, setTranscript] = React.useState<Transcript | null>(null);
  const [exchanges, setExchanges] = React.useState<TranscriptExchange[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fetchPage = React.useCallback(
    async (offset: number) => {
      if (!linkId) return;
      setLoading(true);
      setError(null);
      try {
        // Absolute /api/ path: the fetch wrapper routes these past API_BASE
        // (pinned to /api/v1) to the origin, so a v2 route is reachable.
        const res = await apiGet<Transcript>(
          `/api/v2/assessments/transcripts/links/${linkId}?limit=${PAGE}&offset=${offset}`
        );
        setTranscript(res);
        setExchanges((prev) =>
          offset === 0 ? res.exchanges : [...prev, ...res.exchanges]
        );
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Couldn't load the transcript";
        setError(message);
        toast({
          title: "Transcript unavailable",
          description: message,
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    },
    [linkId, toast]
  );

  React.useEffect(() => {
    if (!open || !linkId) return;
    setTranscript(null);
    setExchanges([]);
    void fetchPage(0);
  }, [open, linkId, fetchPage]);

  const loadedAll = transcript ? exchanges.length >= transcript.total : true;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Assessment transcript, {candidateName}
            {jobTitle ? ` (${jobTitle})` : ""}
          </DialogTitle>
          <DialogDescription>
            Every question this candidate was asked and every answer they gave,
            exactly as submitted.
          </DialogDescription>
        </DialogHeader>

        {loading && exchanges.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
          </div>
        ) : error ? (
          <p className="py-10 text-center text-sm">{error}</p>
        ) : (
          <div className="space-y-4">
            {transcript ? (
              <p className="text-sm">
                {STATUS_COPY[transcript.status] ?? transcript.status}
                {transcript.total > 0
                  ? ` Showing ${exchanges.length} of ${transcript.total} answered.`
                  : ""}
              </p>
            ) : null}

            {exchanges.length === 0 ? (
              <p className="py-8 text-center text-sm">
                No answers have been recorded for this candidate yet.
              </p>
            ) : (
              <ol className="space-y-3">
                {exchanges.map((exchange) => (
                  <Exchange
                    key={`${exchange.ordinal}-${exchange.follow_up}`}
                    exchange={exchange}
                  />
                ))}
              </ol>
            )}

            {!loadedAll ? (
              <Button
                variant="outline"
                className="w-full"
                disabled={loading}
                onClick={() => void fetchPage(exchanges.length)}
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  "Load more"
                )}
              </Button>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

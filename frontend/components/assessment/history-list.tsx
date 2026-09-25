"use client";

// The answers already given, read-only (Appendix B section 3: "Fixed order,
// no skipping. Past answers viewable, never editable").
//
// THE SERVER'S WORDS, VERBATIM. Every line comes from the conversation's
// `history`: the question as the candidate read it and the answer as the
// server recorded it, which for a spoken answer is its final transcript, for
// a structured answer is the server's own rendering, and for a turn that ran
// out of time with nothing given is the server's own sentence saying so. Nothing here
// re-words, summarises or re-orders an entry, because the transcript a
// recruiter reads is written from the same record and the two must agree.
//
// THERE IS NO EDIT CONTROL, AND NONE CAN BE ADDED WITHOUT A ROUTE. The PATCH
// that let the latest prose answer be edited is deleted on the server. An
// answer is evaluated as it was given; letting it be rewritten after the next
// question has been seen would let the next question inform the last answer.

import * as React from "react";
import { Sparkles } from "lucide-react";

import type { HistoryEntry } from "@/lib/assessment/contracts";

export function HistoryList({
  entries,
  initials,
}: {
  entries: HistoryEntry[];
  /** The candidate's initials for their own bubbles. */
  initials: string;
}) {
  if (entries.length === 0) return null;
  return (
    <ol className="space-y-4" aria-label="Your answers so far" data-testid="history-list">
      {entries.map((entry, index) => (
        <li key={index} className="space-y-3">
          <div className="sm:mr-10">
            <p className="sr-only">Question</p>
            <div className="rounded-2xl rounded-tl-md border border-border bg-surface p-5 text-sm leading-7 shadow-card">
              <div className="mb-3 flex items-center gap-2 text-xs">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-100 text-brand-700">
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span className="font-semibold">AI Assessor</span>
              </div>
              <p className="whitespace-pre-wrap">{entry.question}</p>
            </div>
          </div>
          <div className="sm:ml-10">
            <p className="sr-only">Your answer</p>
            <div className="rounded-2xl rounded-tr-md border border-brand-600/30 bg-brand-100/70 p-5 text-sm leading-7">
              <div className="mb-3 flex items-center gap-2 text-xs">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-600 font-semibold text-white">
                  {initials.toUpperCase()}
                </span>
                <span className="font-semibold">You</span>
              </div>
              <p className="whitespace-pre-wrap" data-testid="answer-bubble">
                {entry.answer}
              </p>
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

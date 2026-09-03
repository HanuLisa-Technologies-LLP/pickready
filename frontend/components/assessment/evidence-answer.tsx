"use client";

// The evidence-based question (assessment spec 2.1), the hero format.
//
// A generous text area with guidance about WHAT makes a good answer, never
// about how it is graded. The question itself names the resume item being
// probed; the candidate's job is to say what they personally did, why, and
// what happened, in their own words.

import * as React from "react";

import { TextAnswerField } from "@/components/assessment/text-answer-field";
import type { AnswerComponentProps } from "@/lib/assessment/contracts";
import { textOf } from "@/lib/assessment/answers";

/** Taller than the short-answer box: this answer is expected to be a real
 *  account rather than a paragraph, and a box that looks small invites a small
 *  answer. */
const ROWS = 8;

export function EvidenceAnswer({
  question,
  value,
  onChange,
  disabled,
  fieldHooks,
  onSubmitShortcut,
}: AnswerComponentProps) {
  return (
    <div className="space-y-2">
      <p className="text-sm">
        Be specific about what you personally did, why you did it that way, and
        what happened as a result. Naming what was hard, or what you would do
        differently, is welcome.
      </p>
      <TextAnswerField
        id={`answer-${question.id}`}
        ariaLabel="Your answer"
        value={textOf(value)}
        onChange={(text) => onChange({ text })}
        disabled={disabled}
        fieldHooks={fieldHooks}
        onSubmitShortcut={onSubmitShortcut}
        placeholder="Walk through what you did, in your own words."
        rows={ROWS}
        // Grows with the answer instead of scrolling inside fixed rows.
        // Capped so it cannot push the question off the top of the screen.
        className="max-h-[50vh] min-h-[12rem] resize-y"
      />
    </div>
  );
}

"use client";

// The short-answer format (assessment spec 2.6): the box the assessment has
// always had, brought under the shared contract so it renders, captures and
// autosaves like every other format.

import * as React from "react";

import { TextAnswerField } from "@/components/assessment/text-answer-field";
import type { AnswerComponentProps } from "@/lib/assessment/contracts";
import { textOf } from "@/lib/assessment/answers";

const ROWS = 5;

export function ShortAnswer({
  question,
  value,
  onChange,
  disabled,
  fieldHooks,
  onSubmitShortcut,
}: AnswerComponentProps) {
  return (
    <TextAnswerField
      id={`answer-${question.id}`}
      ariaLabel="Your answer"
      value={textOf(value)}
      onChange={(text) => onChange({ text })}
      disabled={disabled}
      fieldHooks={fieldHooks}
      onSubmitShortcut={onSubmitShortcut}
      placeholder="Share a specific, honest example."
      rows={ROWS}
      className="max-h-[40vh] min-h-[8rem] resize-y"
    />
  );
}

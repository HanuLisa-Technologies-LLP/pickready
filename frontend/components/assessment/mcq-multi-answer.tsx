"use client";

// Multiple choice, more than one correct option (assessment spec 2.3).
//
// A checkbox group with the instruction the spec requires: either how many to
// pick, when the question fixes it, or "select all that apply". The
// select-everything strategy scores zero on the server, and the candidate is
// not told that; they are told how many the question expects, which is the
// honest version of the same information.

import * as React from "react";

import { Checkbox } from "@/components/ui/checkbox";
import type { AnswerComponentProps, McqPayloadView } from "@/lib/assessment/contracts";
import { isMcqMultiAnswer } from "@/lib/assessment/answers";
import { fieldEventProps } from "@/lib/assessment/field-events";
import { numberInWords } from "@/lib/assessment/words";

export function selectionInstruction(selectCount: number | null): string {
  if (selectCount === null) return "Select all that apply.";
  return `Select ${numberInWords(selectCount)} option${selectCount === 1 ? "" : "s"}.`;
}

export function McqMultiAnswer({
  question,
  prompt,
  value,
  onChange,
  disabled,
  fieldHooks,
}: AnswerComponentProps) {
  const payload = question.payload as McqPayloadView;
  const selected = isMcqMultiAnswer(value) ? value.selected_option_ids : [];
  const events = React.useMemo(() => fieldEventProps(fieldHooks), [fieldHooks]);
  const instructionId = `instruction-${question.id}`;

  const toggle = (optionId: string, checked: boolean) => {
    // Kept in the candidate's option order rather than click order, so the
    // payload is the same whichever sequence the boxes were ticked in.
    const next = payload.options
      .map((option) => option.id)
      .filter((id) => (id === optionId ? checked : selected.includes(id)));
    onChange({ selected_option_ids: next });
  };

  return (
    <div
      role="group"
      aria-label={prompt}
      aria-describedby={instructionId}
      className="space-y-2"
      // See the single-answer component: the whole hook set on the group,
      // because every one of these events bubbles from the option that
      // received it.
      {...events}
    >
      <p id={instructionId} className="text-sm font-medium">
        {selectionInstruction(payload.select_count)}
      </p>
      {payload.options.map((option) => {
        const checked = selected.includes(option.id);
        return (
          <label
            key={option.id}
            className="flex cursor-pointer items-start gap-3 border border-input bg-surface p-3 text-sm leading-6 transition-[border-color,background-color] duration-150 hover:border-field-hover has-[[data-state=checked]]:border-navy-600 has-[[data-state=checked]]:bg-navy-50"
          >
            <Checkbox
              checked={checked}
              disabled={disabled}
              className="mt-1"
              onCheckedChange={(state) => toggle(option.id, state === true)}
              onClick={(event) => fieldHooks.onOptionClick(event.timeStamp)}
            />
            <span>{option.text}</span>
          </label>
        );
      })}
    </div>
  );
}

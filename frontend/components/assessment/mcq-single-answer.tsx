"use client";

// Multiple choice, one correct option (assessment spec 2.2).
//
// A radio group, so the keyboard works the way a candidate expects: arrows
// move and select, Tab leaves the group. The options arrive in THIS
// candidate's order from the server and are rendered in that order; nothing
// here sorts, shuffles or hints at which one is right, because the correct id
// never reaches the browser.

import * as React from "react";

import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { AnswerComponentProps, McqPayloadView } from "@/lib/assessment/contracts";
import { isMcqSingleAnswer } from "@/lib/assessment/answers";
import { fieldEventProps } from "@/lib/assessment/field-events";

export function McqSingleAnswer({
  question,
  prompt,
  value,
  onChange,
  disabled,
  fieldHooks,
}: AnswerComponentProps) {
  const payload = question.payload as McqPayloadView;
  const selected = isMcqSingleAnswer(value) ? value.selected_option_id : "";
  const events = React.useMemo(() => fieldEventProps(fieldHooks), [fieldHooks]);

  return (
    <RadioGroup
      value={selected}
      disabled={disabled}
      onValueChange={(next) => onChange({ selected_option_id: next })}
      aria-label={prompt}
      className="gap-2"
      // The whole hook set on the group. Focus, blur, keydown and scroll all
      // bubble from whichever option holds them, so one spread records the
      // group as one field, and a drop aimed at an option is refused and
      // counted exactly as one aimed at a text box is.
      {...events}
    >
      {payload.options.map((option) => (
        <label
          key={option.id}
          className="flex cursor-pointer items-start gap-3 border border-input bg-surface p-3 text-sm leading-6 transition-[border-color,background-color] duration-150 hover:border-field-hover has-[[data-state=checked]]:border-navy-600 has-[[data-state=checked]]:bg-navy-50"
        >
          <RadioGroupItem
            value={option.id}
            className="mt-1"
            // On the control rather than the label: a click on the label text
            // is forwarded to the control by the browser, so a handler on both
            // would count one considered click as two rapid ones.
            onClick={(event) => fieldHooks.onOptionClick(event.timeStamp)}
          />
          <span>{option.text}</span>
        </label>
      ))}
    </RadioGroup>
  );
}

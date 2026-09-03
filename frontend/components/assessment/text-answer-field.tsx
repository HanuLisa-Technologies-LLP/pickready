"use client";

// The prose answer box shared by the evidence-based and short-answer formats.
//
// One textarea, no toolbar. A formatting toolbar is a way to obscure input
// (assessment spec 2.1) and it is also a way to paste around the clipboard
// block, so the two prose formats differ only in their guidance and their
// size, and both come through here.

import * as React from "react";

import { Textarea } from "@/components/ui/textarea";
import type { ProctoringFieldHooks } from "@/lib/assessment/contracts";
import { fieldEventProps } from "@/lib/assessment/field-events";

/** The backend rejects an answer over this length with a 422
 *  (`ConversationMessageIn.answer`). Enforced here so a candidate who has just
 *  written a long, careful answer is stopped at the boundary rather than
 *  losing it to a validation error after they press Send. */
export const MAX_ANSWER = 10000;

/** Show the counter only when it starts to matter. A permanent character count
 *  on an interview answer reads as a word limit and makes people write to it. */
export const COUNTER_FROM = MAX_ANSWER - 1000;

export function TextAnswerField({
  id,
  value,
  onChange,
  disabled,
  fieldHooks,
  onSubmitShortcut,
  placeholder,
  rows,
  className,
  ariaLabel,
  inputRef,
}: {
  id: string;
  value: string;
  onChange: (text: string) => void;
  disabled: boolean;
  fieldHooks: ProctoringFieldHooks;
  onSubmitShortcut: () => void;
  placeholder: string;
  rows: number;
  className: string;
  ariaLabel: string;
  inputRef?: React.Ref<HTMLTextAreaElement>;
}) {
  const events = React.useMemo(
    () => fieldEventProps(fieldHooks, onSubmitShortcut),
    [fieldHooks, onSubmitShortcut]
  );

  return (
    <div className="space-y-2">
      <label htmlFor={id} className="sr-only">
        {ariaLabel}
      </label>
      <Textarea
        ref={inputRef}
        id={id}
        rows={rows}
        value={value}
        disabled={disabled}
        maxLength={MAX_ANSWER}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={className}
        {...events}
      />
      <p className="text-xs">
        {/* Stated rather than left to be discovered: a candidate who assumes
            Enter sends has already lost a paragraph by the time they find out
            it does not. */}
        Press Ctrl+Enter (Cmd+Enter on Mac) to send.
        {value.length >= COUNTER_FROM ? (
          <span className="ml-2 font-medium">
            {MAX_ANSWER - value.length} characters left
          </span>
        ) : null}
      </p>
    </div>
  );
}

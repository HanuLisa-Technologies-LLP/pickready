"use client";

// Fill in the blank (assessment spec 2.4).
//
// The inputs sit INSIDE the sentence, where the blanks are, sized to the
// answer the server expects without revealing it: `expected_length` is the
// length of the longest accepted answer and nothing more. A blank rendered as
// a separate labelled field below the sentence would make the candidate hold
// the sentence in their head while typing; inline, they read and complete it
// in one motion.

import * as React from "react";

import type { AnswerComponentProps, FillBlankPayloadView } from "@/lib/assessment/contracts";
import { BLANK_MARKER, isFillBlankAnswer } from "@/lib/assessment/answers";
import { fieldEventProps } from "@/lib/assessment/field-events";
import { numberInWords } from "@/lib/assessment/words";

/** Mirrors `services/assessment_formats/types.MAX_BLANK_CHARS`. */
export const MAX_BLANK_CHARS = 200;

/** Room beyond the expected length so the last character is not typed
 *  against the edge of the box, which reads as "no more will fit". */
const BLANK_SIZE_PADDING = 2;
/** A blank narrower than this is hard to hit on a phone. */
const BLANK_SIZE_MIN = 4;
/** A blank wider than this would overflow a phone's width; the input scrolls
 *  inside itself past this point. */
const BLANK_SIZE_MAX = 40;

export function blankSize(expectedLength: number): number {
  return Math.min(BLANK_SIZE_MAX, Math.max(BLANK_SIZE_MIN, expectedLength + BLANK_SIZE_PADDING));
}

export function FillBlankAnswer({
  question,
  prompt,
  value,
  onChange,
  disabled,
  fieldHooks,
  onSubmitShortcut,
}: AnswerComponentProps) {
  const payload = question.payload as FillBlankPayloadView;
  const values = isFillBlankAnswer(value) ? value.values : payload.blanks.map(() => "");
  const events = React.useMemo(
    () => fieldEventProps(fieldHooks, onSubmitShortcut),
    [fieldHooks, onSubmitShortcut]
  );
  const segments = payload.template.split(BLANK_MARKER);
  const caseSensitive = payload.blanks.filter((blank) => blank.case_sensitive);

  const setValue = (index: number, next: string) => {
    const updated = payload.blanks.map((_, position) =>
      position === index ? next : (values[position] ?? "")
    );
    onChange({ values: updated });
  };

  return (
    <fieldset className="space-y-3" aria-label={prompt}>
      <p className="whitespace-pre-wrap text-sm leading-8">
        {segments.map((segment, index) => {
          const blank = payload.blanks[index];
          return (
            <React.Fragment key={index}>
              {segment}
              {index < segments.length - 1 && blank ? (
                <input
                  type="text"
                  inputMode="text"
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  aria-label={`Blank ${numberInWords(blank.index + 1)}`}
                  size={blankSize(blank.expected_length)}
                  maxLength={MAX_BLANK_CHARS}
                  value={values[index] ?? ""}
                  disabled={disabled}
                  onChange={(event) => setValue(index, event.target.value)}
                  className="mx-1 inline-block h-8 border-0 border-b-2 border-input bg-surface px-1 align-baseline font-medium transition-[border-color] duration-150 hover:border-field-hover focus-visible:border-navy-600 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                  {...events}
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </p>
      <p className="text-xs">
        Press Ctrl+Enter (Cmd+Enter on Mac) to send.
        {caseSensitive.length > 0 ? (
          <span className="ml-2">
            Capitalisation matters for{" "}
            {caseSensitive.length === 1
              ? `blank ${numberInWords(caseSensitive[0].index + 1)}.`
              : `blanks ${caseSensitive.map((blank) => numberInWords(blank.index + 1)).join(", ")}.`}
          </span>
        ) : null}
      </p>
    </fieldset>
  );
}

"use client";

// One question, rendered as the control its kind requires.
//
// THE CONTROL IS NOT THE ENFORCEMENT. The API refuses a free-text answer to a
// forced scale and an adjective where observable evidence was asked for, and it
// does so whether or not this file renders the right control. What this file
// does is make the right answer the easy one, and show the refusal next to the
// field that caused it rather than as a toast that scrolls away.

import * as React from "react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import type { EvidenceExample, Question, Section } from "./types";

/**
 * A forced scale, drawn as its positions with the two poles named at each end.
 *
 * Not a slider. A slider suggests a continuum and hides which position is
 * selected until you look closely, and the Runbook's scale is five discrete
 * positions with a real midpoint. The midpoint is labelled so it reads as an
 * answer rather than as an unanswered control.
 */
function ScaleField({
  question,
  value,
  onChange,
  disabled,
}: {
  question: Question;
  value: number | null;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  const min = question.scale_min ?? 1;
  const max = question.scale_max ?? 5;
  const positions = Array.from({ length: max - min + 1 }, (_, i) => min + i);
  const [low, high] = question.poles ?? ["", ""];
  const midpoint = Math.round((min + max) / 2);

  return (
    <div role="radiogroup" aria-label={question.prompt} className="mt-3">
      <div className="flex items-start justify-between gap-4 text-sm font-medium">
        <span className="max-w-[45%]">{low}</span>
        <span className="max-w-[45%] text-right">{high}</span>
      </div>
      <div className="mt-2 flex items-stretch gap-2">
        {positions.map((position) => {
          const selected = value === position;
          return (
            <button
              key={position}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={
                position === min
                  ? low
                  : position === max
                    ? high
                    : position === midpoint
                      ? "No preference between them"
                      : `Between ${low} and ${high}`
              }
              disabled={disabled}
              onClick={() => onChange(position)}
              className={cn(
                "flex-1 rounded-lg border px-3 py-3 text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400",
                selected
                  ? "border-navy-600 bg-navy-600 text-white"
                  : "border-field-border bg-surface hover:bg-navy-50",
                disabled && "opacity-60"
              )}
            >
              {position === midpoint ? "No preference" : ""}
              <span className="sr-only">
                Position {position - min + 1} of {positions.length}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The Runbook's own accepted and rejected pair, shown beside the section that
 * asks for observable evidence.
 *
 * Shown rather than paraphrased. The pair is the quality bar, and a client who
 * reads one refused answer next to its accepted rewrite converts the next one
 * themselves. Teal marks the accepted half, which is the one colour in this
 * system that means "this is what evidence looks like".
 */
export function EvidenceExamples({ examples }: { examples: EvidenceExample[] }) {
  if (examples.length === 0) return null;
  return (
    <div className="mt-4 rounded-xl border border-border bg-navy-50/60 p-4">
      <p className="text-sm font-semibold">What we can and cannot work with</p>
      <dl className="mt-3 space-y-4">
        {examples.map((example) => (
          <div key={example.rejected} className="space-y-2 text-sm leading-6">
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide">
                Not this
              </dt>
              <dd className="mt-1 line-through decoration-1">{example.rejected}</dd>
            </div>
            {/* A teal FILL rather than a left rule. Teal-50 is the
                evidence-supported surface in this system, and a thick
                one-sided border is a generic tell the design gate refuses
                outside the two places it is already semantic. */}
            <div className="rounded-lg bg-teal-50 p-3">
              <dt className="text-xs font-semibold uppercase tracking-wide text-teal-700">
                This
              </dt>
              <dd className="mt-1">{example.accepted}</dd>
            </div>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function QuestionField({
  question,
  section,
  value,
  refusal,
  disabled,
  onChange,
}: {
  question: Question;
  section: Section;
  value: unknown;
  refusal: string | null;
  disabled?: boolean;
  onChange: (next: unknown) => void;
}) {
  const id = `dna-${question.key}`;
  const describedBy = [
    question.help_text ? `${id}-help` : null,
    refusal ? `${id}-refusal` : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="py-6 first:pt-0">
      <Label htmlFor={id} className="text-sm font-semibold leading-6">
        {question.prompt}
        {question.required ? null : (
          <span className="ml-2 text-xs font-medium">Optional</span>
        )}
      </Label>
      {question.help_text ? (
        <p id={`${id}-help`} className="mt-1 text-sm leading-6">
          {question.help_text}
        </p>
      ) : null}

      {question.kind === "scale" ? (
        <ScaleField
          question={question}
          value={typeof value === "number" ? value : null}
          onChange={onChange}
          disabled={disabled}
        />
      ) : question.kind === "choice" ? (
        <div className="mt-3 grid gap-2" role="radiogroup" aria-label={question.prompt}>
          {question.options.map((option) => {
            const selected = value === option;
            return (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={disabled}
                onClick={() => onChange(option)}
                className={cn(
                  "rounded-lg border px-4 py-3 text-left text-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400",
                  selected
                    ? "border-navy-600 bg-navy-50 font-medium"
                    : "border-field-border bg-surface hover:bg-navy-50"
                )}
              >
                {option}
              </button>
            );
          })}
        </div>
      ) : question.kind === "text" ? (
        <Textarea
          id={id}
          aria-describedby={describedBy || undefined}
          aria-invalid={refusal ? true : undefined}
          className="mt-3"
          rows={3}
          disabled={disabled}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : question.kind === "evidence" ? (
        <Textarea
          id={id}
          aria-describedby={describedBy || undefined}
          aria-invalid={refusal ? true : undefined}
          className="mt-3"
          rows={3}
          disabled={disabled}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <EvidenceListField
          id={id}
          section={section}
          describedBy={describedBy}
          invalid={Boolean(refusal)}
          disabled={disabled}
          value={typeof value === "string" ? value : ""}
          onChange={onChange}
        />
      )}

      {refusal ? (
        <p
          id={`${id}-refusal`}
          role="alert"
          className="mt-3 rounded-lg border border-warning bg-warning/10 px-4 py-3 text-sm leading-6"
        >
          {refusal}
        </p>
      ) : null}

      {question.kind === "evidence_list" || question.kind === "evidence" ? (
        <EvidenceExamples examples={section.examples} />
      ) : null}
    </div>
  );
}

/**
 * Several observable statements, one per line.
 *
 * A repeating field rather than one box, because §16 asks for a stated NUMBER
 * of items and the server judges each line on its own. Rendering it as a single
 * paragraph would let a client write one long sentence and be refused for a
 * count they never saw asked for.
 */
function EvidenceListField({
  id,
  section,
  describedBy,
  invalid,
  disabled,
  value,
  onChange,
}: {
  id: string;
  section: Section;
  describedBy: string;
  invalid: boolean;
  disabled?: boolean;
  value: string;
  onChange: (next: string) => void;
}) {
  const minimum = section.min_items ?? 1;
  const maximum = section.max_items ?? Math.max(minimum, 6);
  const existing = value ? value.split("\n") : [];
  const slots = Math.min(
    maximum,
    Math.max(minimum, existing.length + (existing.length < maximum ? 1 : 0))
  );
  const lines = Array.from({ length: slots }, (_, index) => existing[index] ?? "");

  const write = (index: number, next: string) => {
    const updated = [...lines];
    updated[index] = next;
    onChange(updated.map((line) => line.trim()).filter(Boolean).join("\n"));
  };

  return (
    <div className="mt-3 space-y-2">
      {section.item_format ? (
        <p className="text-sm font-medium">{section.item_format}</p>
      ) : null}
      {lines.map((line, index) => (
        <Input
          key={index}
          id={index === 0 ? id : `${id}-${index}`}
          aria-label={`Item ${index + 1}`}
          aria-describedby={index === 0 ? describedBy || undefined : undefined}
          aria-invalid={index === 0 && invalid ? true : undefined}
          disabled={disabled}
          value={line}
          onChange={(event) => write(index, event.target.value)}
        />
      ))}
    </div>
  );
}

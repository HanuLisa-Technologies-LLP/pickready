"use client";

// The six mandatory application fields (spec §7).
//
// Submitted with the resume, before the candidate reaches the assessment. They
// are CAPTURED, never scored: no agent reads them, no grade is attached, and
// the recruiter sees the answer to "Why does this role interest you?" exactly
// as it was typed. The recruiter, not any agent, decides whether a candidate's
// stated interest is genuine.
//
// The field list is served by the backend (`apply-context.validation_fields`)
// rather than hardcoded here, so the form a candidate fills in and the answers
// the report renders cannot drift apart.

import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface ValidationFieldSpec {
  key: string;
  label: string;
  type: "text" | "select" | "date" | "textarea" | string;
  options?: string[];
  hint?: string;
  /** A worked example shown inside the empty box (spec 15). Served by the
   *  backend rather than written here, because the report renders the same
   *  fields this form collects and an example that lived only in the frontend
   *  is one the report could not explain. */
  placeholder?: string;
  /** "INR" on the CTC fields. Rendered as a prefix so the unit is visible in a
   *  filled box, not only in an empty one. */
  currency?: string;
  /** Named documents the readiness answer refers to. Served by the backend so
   *  a candidate is never asked to attest to a set they cannot see. */
  documents?: string[];
}

export type ValidationValues = Record<string, string>;

/**
 * How a currency code is written in front of an amount.
 *
 * "Rs." rather than the rupee sign, deliberately. The glyph renders as a box in
 * several of the fonts this form is read in, and a box in front of a salary is
 * worse than three plain letters. There is one entry because the product
 * collects one currency; the map exists so adding a second is data.
 */
const CURRENCY_PREFIX: Record<string, string> = { INR: "Rs." };

/** Mirrors `application_validation.ROLE_INTEREST_MIN_CHARS` on the server. */
export const ROLE_INTEREST_MIN_CHARS = 30;

/**
 * Which fields are still unanswered, by label. Mirrors the server's own check
 * so the candidate is told before the upload starts rather than after: a 422
 * after a 10 MB resume has already been sent is a bad way to learn you missed a
 * dropdown.
 */
export function missingValidationFields(
  fields: ValidationFieldSpec[],
  values: ValidationValues
): string[] {
  const missing = fields
    .filter((field) => !(values[field.key] ?? "").trim())
    .map((field) => field.label);
  const interest = (values.role_interest ?? "").trim();
  if (interest && interest.length < ROLE_INTEREST_MIN_CHARS) {
    missing.push("Why does this role interest you? (please write at least a sentence)");
  }
  return missing;
}

/** Fallback only. The real copy is served with the field list
 *  (`apply-context.validation_intro`) so it cannot drift from the behaviour. */
export const VALIDATION_INTRO_FALLBACK =
  "The following information is mandatory. You need to fill this information " +
  "only one time and automatically applicable to all other jobs which you " +
  "apply, otherwise you edit.";

export function ApplicationValidationForm({
  fields,
  values,
  onChange,
  disabled,
  intro,
}: {
  fields: ValidationFieldSpec[];
  values: ValidationValues;
  onChange: (next: ValidationValues) => void;
  disabled?: boolean;
  intro?: string;
}) {
  if (fields.length === 0) return null;
  const set = (key: string, value: string) => onChange({ ...values, [key]: value });

  return (
    <section className="space-y-4" aria-label="Required application details">
      <div>
        <h3 className="text-sm font-semibold">
          {intro?.trim() || VALIDATION_INTRO_FALLBACK}
        </h3>
        <p className="mt-1 text-xs">
          Shared with the hiring team exactly as you write them. Nothing here is scored.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {fields
          .filter((field) => field.type !== "textarea")
          .map((field) => (
            <div key={field.key} className="space-y-1.5">
              <Label htmlFor={`validation-${field.key}`}>
                {field.label}
                <span aria-hidden className="ml-0.5">
                  *
                </span>
              </Label>
              {field.type === "select" ? (
                <Select
                  value={values[field.key] ?? ""}
                  disabled={disabled}
                  onValueChange={(value) => set(field.key, value)}
                >
                  <SelectTrigger id={`validation-${field.key}`}>
                    <SelectValue placeholder="Select one" />
                  </SelectTrigger>
                  <SelectContent>
                    {(field.options ?? []).map((option) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : field.currency ? (
                // The unit sits BESIDE the box rather than inside the
                // placeholder, so it is still visible once the candidate has
                // typed. A placeholder disappears at the first keystroke, which
                // is exactly when "is this per month or per year, in what
                // currency" becomes the question.
                <div className="flex items-stretch">
                  <span className="inline-flex items-center rounded-l-md border border-r-0 px-3 text-sm">
                    {CURRENCY_PREFIX[field.currency] ?? field.currency}
                  </span>
                  <Input
                    id={`validation-${field.key}`}
                    type="text"
                    inputMode="numeric"
                    className="rounded-l-none"
                    required
                    disabled={disabled}
                    placeholder={field.placeholder}
                    value={values[field.key] ?? ""}
                    onChange={(event) => set(field.key, event.target.value)}
                  />
                </div>
              ) : (
                <Input
                  id={`validation-${field.key}`}
                  type={field.type === "date" ? "date" : "text"}
                  required
                  disabled={disabled}
                  placeholder={field.placeholder}
                  value={values[field.key] ?? ""}
                  onChange={(event) => set(field.key, event.target.value)}
                />
              )}
              {field.hint ? <p className="text-xs">{field.hint}</p> : null}
            </div>
          ))}
      </div>
      {/* The named document set. It sits outside the two-column grid because a
          nine-item list inside a half-width cell is unreadable, and it is
          rendered from the field spec rather than typed here so the list a
          candidate attests to is the list the product actually asks for. */}
      {fields
        .filter((field) => (field.documents ?? []).length > 0)
        .map((field) => (
          <div
            key={`${field.key}-documents`}
            className="rounded-md border p-3"
            data-testid={`validation-documents-${field.key}`}
          >
            <p className="text-xs font-semibold">
              Documents required at onboarding
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
              {(field.documents ?? []).map((document) => (
                <li key={document}>{document}</li>
              ))}
            </ul>
          </div>
        ))}
      {fields
        .filter((field) => field.type === "textarea")
        .map((field) => (
          <div key={field.key} className="space-y-1.5">
            <Label htmlFor={`validation-${field.key}`}>
              {field.label}
              <span aria-hidden className="ml-0.5">
                *
              </span>
            </Label>
            <Textarea
              id={`validation-${field.key}`}
              rows={4}
              required
              disabled={disabled}
              value={values[field.key] ?? ""}
              onChange={(event) => set(field.key, event.target.value)}
            />
            {field.hint ? <p className="text-xs">{field.hint}</p> : null}
          </div>
        ))}
    </section>
  );
}

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
}

export type ValidationValues = Record<string, string>;

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

export function ApplicationValidationForm({
  fields,
  values,
  onChange,
  disabled,
}: {
  fields: ValidationFieldSpec[];
  values: ValidationValues;
  onChange: (next: ValidationValues) => void;
  disabled?: boolean;
}) {
  if (fields.length === 0) return null;
  const set = (key: string, value: string) => onChange({ ...values, [key]: value });

  return (
    <section className="space-y-4" aria-label="Required application details">
      <div>
        <h3 className="text-sm font-semibold">A few required details</h3>
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
              ) : (
                <Input
                  id={`validation-${field.key}`}
                  type={field.type === "date" ? "date" : "text"}
                  required
                  disabled={disabled}
                  value={values[field.key] ?? ""}
                  onChange={(event) => set(field.key, event.target.value)}
                />
              )}
              {field.hint ? <p className="text-xs">{field.hint}</p> : null}
            </div>
          ))}
      </div>
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

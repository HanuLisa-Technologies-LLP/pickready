"use client";

// Renders the 40-aspect questionnaire as editable inputs (candidate outreach)
// grouped by category.

import * as React from "react";

import { ASPECTS, aspectDisplayNo, type AspectDef } from "@/lib/aspects";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type AspectAnswers = Record<number, string | number | boolean | null>;

// ASSUMPTION: the PRD does not mark individual aspects optional. A form of
// hard-required questions is a hostile form, so the genuinely discretionary
// free-text items are optional and everything else is required. Booleans are
// otherwise never "missing", an untouched switch reads as an explicit No.
// Ids 13 and 33 were retired on 2026-08-09 and are gone from this list with the
// questions themselves.
export const OPTIONAL_ASPECT_IDS = [12, 18, 22, 27, 35];

// The two Verification & Consent items are MANDATORY (client decision,
// 2026-08-09): they used to render "(optional)" purely because they are
// booleans and a switch has no unanswered state. They are now asked as an
// explicit Yes / No with no preselected value, so "required" means the
// candidate STATED a position -- it does not mean they must consent. A default
// that reads as consent nobody chose would be worse than the label was.
export const REQUIRED_BOOLEAN_ASPECT_IDS = [39, 40];

function isOptional(aspect: AspectDef): boolean {
  if (REQUIRED_BOOLEAN_ASPECT_IDS.includes(aspect.id)) return false;
  return OPTIONAL_ASPECT_IDS.includes(aspect.id) || aspect.type === "boolean";
}

function isAnswered(value: string | number | boolean | null | undefined): boolean {
  if (typeof value === "boolean") return true;
  if (value === null || value === undefined) return false;
  return String(value).trim() !== "";
}

/** Aspects the candidate still has to answer, in questionnaire order. */
export function missingAspects(
  answers: AspectAnswers,
  excludeIds: number[] = []
): AspectDef[] {
  return ASPECTS.filter(
    (a) =>
      !excludeIds.includes(a.id) &&
      !isOptional(a) &&
      !isAnswered(answers[a.id])
  );
}

/** Answered / total across the required aspects, for the progress meter. */
export function aspectProgress(
  answers: AspectAnswers,
  excludeIds: number[] = []
): { answered: number; total: number; percent: number } {
  const required = ASPECTS.filter(
    (a) => !excludeIds.includes(a.id) && !isOptional(a)
  );
  const answered = required.filter((a) => isAnswered(answers[a.id])).length;
  const total = required.length;
  return {
    answered,
    total,
    percent: total === 0 ? 100 : Math.round((answered / total) * 100),
  };
}

function AspectControl({
  aspect,
  value,
  onChange,
  invalid,
}: {
  aspect: AspectDef;
  value: string | number | boolean | null | undefined;
  onChange: (v: string | number | boolean | null) => void;
  invalid?: boolean;
}) {
  const flag = invalid
    ? { "aria-invalid": true as const, className: "border-destructive" }
    : {};
  switch (aspect.type) {
    case "boolean":
      // A mandatory consent is asked, not toggled: no preselected value, so an
      // untouched control cannot be read back as a Yes the candidate never gave.
      if (REQUIRED_BOOLEAN_ASPECT_IDS.includes(aspect.id)) {
        return (
          <Select
            value={value === true ? "Yes" : value === false ? "No" : ""}
            onValueChange={(v) => onChange(v === "Yes")}
          >
            <SelectTrigger id={`aspect-${aspect.id}`} {...flag}>
              <SelectValue placeholder="Select Yes or No" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Yes">Yes</SelectItem>
              <SelectItem value="No">No</SelectItem>
            </SelectContent>
          </Select>
        );
      }
      return (
        <div className="flex items-center gap-3">
          <Switch
            id={`aspect-${aspect.id}`}
            checked={value === true}
            onCheckedChange={(checked) => onChange(checked)}
          />
          <span className="text-sm">
            {value === true ? "Yes" : "No"}
          </span>
        </div>
      );
    case "number":
      return (
        <Input
          id={`aspect-${aspect.id}`}
          type="number"
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? null : Number(e.target.value))
          }
          {...flag}
        />
      );
    case "date":
      return (
        <Input
          id={`aspect-${aspect.id}`}
          type="date"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value || null)}
          {...flag}
        />
      );
    case "select":
      return (
        <Select
          value={typeof value === "string" ? value : ""}
          onValueChange={(v) => onChange(v)}
        >
          <SelectTrigger id={`aspect-${aspect.id}`} {...flag}>
            <SelectValue placeholder="Select an option" />
          </SelectTrigger>
          <SelectContent>
            {(aspect.options ?? []).map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    default:
      return (
        <Textarea
          id={`aspect-${aspect.id}`}
          rows={2}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
          {...flag}
        />
      );
  }
}

export function AspectsForm({
  answers,
  onChange,
  excludeIds = [],
  invalidIds = [],
}: {
  answers: AspectAnswers;
  onChange: (answers: AspectAnswers) => void;
  /** Aspects already covered by the personal-details section (FR-5.1). */
  excludeIds?: number[];
  /** Aspects flagged as missing by a failed submit attempt. */
  invalidIds?: number[];
}) {
  const visible = ASPECTS.filter((a) => !excludeIds.includes(a.id));
  const categories = Array.from(new Set(visible.map((a) => a.category)));
  const invalid = new Set(invalidIds);
  const progress = aspectProgress(answers, excludeIds);

  return (
    <div className="space-y-8">
      {/* Sticky progress so the length of the form never feels open-ended. */}
      <div className="sticky top-0 z-10 -mx-1 space-y-1.5 bg-background/95 px-1 py-2 backdrop-blur">
        <div className="flex items-center justify-between text-xs font-medium">
          <span>Questionnaire progress</span>
          <span>
            {progress.answered} of {progress.total} answered
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuenow={progress.percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Questionnaire progress"
        >
          <div
            className="h-full bg-foreground transition-all"
            style={{ width: `${progress.percent}%` }}
          />
        </div>
      </div>

      {categories.map((category) => {
        const rows = visible.filter((a) => a.category === category);
        const need = rows.filter((a) => !isOptional(a));
        const done = need.filter((a) => isAnswered(answers[a.id])).length;
        return (
          <fieldset key={category} className="space-y-4">
            <legend className="flex w-full items-baseline justify-between gap-3 border-b pb-1">
              <span className="text-sm font-semibold uppercase tracking-wide">
                {category}
              </span>
              {need.length > 0 ? (
                <span className="text-xs font-normal">
                  {done}/{need.length}
                </span>
              ) : null}
            </legend>
            {rows.map((aspect) => {
              const optional = isOptional(aspect);
              const isInvalid = invalid.has(aspect.id);
              return (
                <div key={aspect.id} className="space-y-2">
                  <Label
                    htmlFor={`aspect-${aspect.id}`}
                    className={cn(isInvalid && "text-destructive")}
                  >
                    <span className="mr-1.5">
                      {aspectDisplayNo(aspect.id) ?? aspect.id}.
                    </span>
                    {aspect.question}
                    {optional ? (
                      <span className="ml-1.5 text-xs font-normal">
                        (optional)
                      </span>
                    ) : (
                      <span className="ml-0.5">*</span>
                    )}
                  </Label>
                  <AspectControl
                    aspect={aspect}
                    value={answers[aspect.id]}
                    onChange={(v) => onChange({ ...answers, [aspect.id]: v })}
                    invalid={isInvalid}
                  />
                  {isInvalid ? (
                    <p className="text-xs font-medium text-destructive">
                      This answer is required.
                    </p>
                  ) : null}
                </div>
              );
            })}
          </fieldset>
        );
      })}
    </div>
  );
}

/** Read-only rendering of answered aspects (HR / Hiring Manager review). */
export function AspectsReadout({
  aspects,
}: {
  aspects: { aspect_id: number; question?: string; answer: unknown }[];
}) {
  const byId = new Map(aspects.map((a) => [a.aspect_id, a]));
  const known = new Set(ASPECTS.map((a) => a.id));
  // Answers to questions retired on 2026-08-09. An application submitted before
  // that date is a record of what the candidate was actually asked, and a
  // recruiter reading it must still see every answer they gave; dropping the
  // rows because the question is no longer on the form would quietly rewrite
  // history. They carry their own stored `question` text.
  const retired = aspects
    .filter((a) => !known.has(a.aspect_id) && a.answer !== null && a.answer !== undefined && a.answer !== "")
    .sort((a, b) => a.aspect_id - b.aspect_id);
  const renderAnswer = (answer: unknown): string => {
    if (answer === null || answer === undefined || answer === "") return "-";
    if (typeof answer === "boolean") return answer ? "Yes" : "No";
    return String(answer);
  };
  return (
    <div className="space-y-6">
      {Array.from(new Set(ASPECTS.map((a) => a.category))).map((category) => {
        const rows = ASPECTS.filter((a) => a.category === category);
        return (
          <div key={category}>
            <h3 className="mb-2 border-b pb-1 text-sm font-semibold uppercase tracking-wide">
              {category}
            </h3>
            <dl className="space-y-2">
              {rows.map((def) => {
                const resp = byId.get(def.id);
                const display = renderAnswer(resp?.answer);
                return (
                  <div
                    key={def.id}
                    className="grid grid-cols-1 gap-1 rounded-md border p-3 sm:grid-cols-2"
                  >
                    <dt className="text-sm">
                      <span className="mr-1.5">{aspectDisplayNo(def.id) ?? def.id}.</span>
                      {resp?.question ?? def.question}
                    </dt>
                    <dd className="text-sm font-medium">{display}</dd>
                  </div>
                );
              })}
            </dl>
          </div>
        );
      })}
      {retired.length > 0 ? (
        <div data-testid="retired-aspects">
          <h3 className="mb-2 border-b pb-1 text-sm font-semibold uppercase tracking-wide">
            Previously asked
          </h3>
          <dl className="space-y-2">
            {retired.map((resp) => (
              <div
                key={resp.aspect_id}
                className="grid grid-cols-1 gap-1 rounded-md border p-3 sm:grid-cols-2"
              >
                <dt className="text-sm">
                  {resp.question ?? `Question ${resp.aspect_id}`}
                </dt>
                <dd className="text-sm font-medium">{renderAnswer(resp.answer)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  );
}

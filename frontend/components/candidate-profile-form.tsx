"use client";

// The candidate's advanced profile form, the 40 validation aspects, answered
// ONCE here instead of being re-asked inside every job's assessment
// conversation (client decision, 2026-07-27).
//
// The form is entirely SERVER-DEFINED: the shape, wording, ordering and option
// lists come from `GET /portal/me/profile-form` (backed by the fixed Python
// constant in services/candidate_profile_form.py). This component renders
// whatever it is handed and never hardcodes a question, so the question bank
// stays a single source of truth on the backend.

import * as React from "react";
import { Save } from "lucide-react";

import { apiGet, apiPut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type FormFieldType =
  | "text"
  | "textarea"
  | "date"
  | "radio"
  | "checkbox_group"
  | "checkbox"
  | "education_table";

export interface FormFieldDef {
  key: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  display_no?: number;
  hint?: string;
  options?: string[];
  rows?: { key: string; label: string }[];
  columns?: { key: string; label: string }[];
}

export interface FormSectionDef {
  key: string;
  title: string;
  description?: string;
  fields: FormFieldDef[];
}

export interface ProfileFormDefinition {
  instructions: string[];
  sections: FormSectionDef[];
}

export type AnswerValue =
  | string
  | boolean
  | string[]
  | Record<string, Record<string, string>>;

export interface ProfileFormPayload {
  definition: ProfileFormDefinition;
  answers: Record<string, AnswerValue>;
  complete: boolean;
  missing: string[];
  updated_at?: string | null;
}

function fieldLabel(field: FormFieldDef): string {
  return field.display_no ? `${field.display_no}. ${field.label}` : field.label;
}

function EducationTable({
  field,
  value,
  disabled,
  onChange,
}: {
  field: FormFieldDef;
  value: Record<string, Record<string, string>>;
  disabled?: boolean;
  onChange: (next: Record<string, Record<string, string>>) => void;
}) {
  const rows = field.rows ?? [];
  const columns = field.columns ?? [];
  const setCell = (rowKey: string, columnKey: string, cell: string) => {
    const row = { ...(value[rowKey] ?? {}), [columnKey]: cell };
    if (!cell) delete row[columnKey];
    const next = { ...value, [rowKey]: row };
    if (Object.keys(row).length === 0) delete next[rowKey];
    onChange(next);
  };
  return (
    // Wide on desktop, horizontally scrollable on a phone, the page body must
    // never scroll sideways.
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-sm">
        <thead>
          <tr>
            <th className="border-b p-2 text-left font-semibold">Education Level</th>
            {columns.map((column) => (
              <th key={column.key} className="border-b p-2 text-left font-semibold">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row" className="border-b p-2 text-left font-medium">
                {row.label}
              </th>
              {columns.map((column) => (
                <td key={column.key} className="border-b p-1">
                  <Input
                    aria-label={`${row.label}, ${column.label}`}
                    value={value[row.key]?.[column.key] ?? ""}
                    disabled={disabled}
                    onChange={(event) => setCell(row.key, column.key, event.target.value)}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Field({
  field,
  value,
  disabled,
  invalid,
  onChange,
}: {
  field: FormFieldDef;
  value: AnswerValue | undefined;
  disabled?: boolean;
  invalid?: boolean;
  onChange: (next: AnswerValue) => void;
}) {
  const id = `profile-${field.key}`;

  if (field.type === "checkbox") {
    return (
      <div className="flex items-start gap-2">
        <input
          id={id}
          type="checkbox"
          className="mt-1 h-4 w-4"
          checked={value === true}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <Label htmlFor={id} className="leading-6">
          {field.label}
          {field.required ? " *" : ""}
        </Label>
      </div>
    );
  }

  const body = (() => {
    switch (field.type) {
      case "education_table":
        return (
          <EducationTable
            field={field}
            value={(value as Record<string, Record<string, string>>) ?? {}}
            disabled={disabled}
            onChange={onChange}
          />
        );
      case "radio":
        return (
          <div className="space-y-1.5" role="radiogroup" aria-labelledby={`${id}-label`}>
            {(field.options ?? []).map((option) => (
              <label key={option} className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name={id}
                  className="mt-1 h-4 w-4"
                  value={option}
                  checked={value === option}
                  disabled={disabled}
                  onChange={() => onChange(option)}
                />
                <span>{option}</span>
              </label>
            ))}
          </div>
        );
      case "checkbox_group": {
        const selected = Array.isArray(value) ? value : [];
        return (
          <div className="space-y-1.5" role="group" aria-labelledby={`${id}-label`}>
            {(field.options ?? []).map((option) => (
              <label key={option} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4"
                  checked={selected.includes(option)}
                  disabled={disabled}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, option]
                        : selected.filter((item) => item !== option)
                    )
                  }
                />
                <span>{option}</span>
              </label>
            ))}
          </div>
        );
      }
      case "textarea":
        return (
          <Textarea
            id={id}
            rows={4}
            value={typeof value === "string" ? value : ""}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
          />
        );
      case "date":
        return (
          <Input
            id={id}
            type="date"
            value={typeof value === "string" ? value : ""}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
          />
        );
      default:
        return (
          <Input
            id={id}
            value={typeof value === "string" ? value : ""}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
          />
        );
    }
  })();

  return (
    <div className="space-y-1.5">
      <Label id={`${id}-label`} htmlFor={id}>
        {fieldLabel(field)}
        {field.required ? " *" : ""}
      </Label>
      {field.hint ? <p className="text-xs">{field.hint}</p> : null}
      {body}
      {invalid ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          This answer is required.
        </p>
      ) : null}
    </div>
  );
}

/**
 * My Profile's advanced form. Saves are PARTIAL by design, a candidate can
 * fill this in across several sittings, so missing required answers are shown
 * as a reminder rather than blocking the save.
 */
export function CandidateProfileForm({
  onSaved,
}: {
  onSaved?: (payload: ProfileFormPayload) => void;
}) {
  const { toast } = useToast();
  const [payload, setPayload] = React.useState<ProfileFormPayload | null>(null);
  const [answers, setAnswers] = React.useState<Record<string, AnswerValue>>({});
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [dirty, setDirty] = React.useState(false);

  React.useEffect(() => {
    apiGet<ProfileFormPayload>("/portal/me/profile-form")
      .then((res) => {
        setPayload(res);
        setAnswers(res.answers ?? {});
      })
      .catch((loadError) => setError(apiErrorMessage(loadError)))
      .finally(() => setLoading(false));
  }, []);

  const setAnswer = (key: string, value: AnswerValue) => {
    setAnswers((current) => ({ ...current, [key]: value }));
    setDirty(true);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const saved = await apiPut<ProfileFormPayload>("/portal/me/profile-form", {
        answers,
      });
      setPayload(saved);
      setAnswers(saved.answers ?? {});
      setDirty(false);
      onSaved?.(saved);
      toast({
        title: "Profile saved",
        description: saved.complete
          ? "Your profile is complete and will be used for every application."
          : "Saved. A few required answers are still outstanding.",
      });
    } catch (saveError) {
      const message = apiErrorMessage(saveError);
      setError(message);
      toast({
        title: "Could not save your profile",
        description: message,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-6 text-sm" role="status">
          Loading your profile
        </CardContent>
      </Card>
    );
  }
  if (!payload) {
    return (
      <Card>
        <CardContent className="p-6">
          <p role="alert" className="text-sm font-medium text-destructive">
            {error ?? "We couldn't load your profile. Please refresh."}
          </p>
        </CardContent>
      </Card>
    );
  }

  // Only flag a required answer once the server has said it's missing, so an
  // untouched form doesn't open covered in red.
  const missing = new Set(dirty ? [] : payload.missing);

  return (
    <form onSubmit={save} noValidate className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Profile details</CardTitle>
          <CardDescription>
            Answered once here and reused on every application, so you never
            retype them per job.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {payload.definition.instructions.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {!payload.complete && payload.missing.length > 0 ? (
            <p className="mt-4 rounded-md border p-3 text-sm font-medium">
              {payload.missing.length} required answer
              {payload.missing.length === 1 ? "" : "s"} still outstanding.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {payload.definition.sections.map((section) => (
        <Card key={section.key}>
          <CardHeader>
            <CardTitle className="text-base">{section.title}</CardTitle>
            {section.description ? (
              <CardDescription>{section.description}</CardDescription>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-5">
            {section.fields.map((field, index) => (
              <React.Fragment key={field.key}>
                {index > 0 ? <Separator /> : null}
                <Field
                  field={field}
                  value={answers[field.key]}
                  disabled={saving}
                  invalid={missing.has(field.key)}
                  onChange={(next) => setAnswer(field.key, next)}
                />
              </React.Fragment>
            ))}
          </CardContent>
        </Card>
      ))}

      {error ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button type="submit" className="gap-2" disabled={saving}>
          <Save className="h-4 w-4" />
          {saving ? "Saving" : "Save profile"}
        </Button>
        {payload.updated_at && !dirty ? (
          <span className="text-sm">
            Last saved {new Date(payload.updated_at).toLocaleString()}
          </span>
        ) : null}
      </div>
    </form>
  );
}

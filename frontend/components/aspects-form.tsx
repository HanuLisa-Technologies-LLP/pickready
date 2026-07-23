"use client";

// Renders the 40-aspect questionnaire as editable inputs (candidate outreach)
// grouped by category.

import * as React from "react";

import { ASPECTS, type AspectDef } from "@/lib/aspects";
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

function AspectControl({
  aspect,
  value,
  onChange,
}: {
  aspect: AspectDef;
  value: string | number | boolean | null | undefined;
  onChange: (v: string | number | boolean | null) => void;
}) {
  switch (aspect.type) {
    case "boolean":
      return (
        <div className="flex items-center gap-3">
          <Switch
            id={`aspect-${aspect.id}`}
            checked={value === true}
            onCheckedChange={(checked) => onChange(checked)}
          />
          <span className="text-sm text-muted-foreground">
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
        />
      );
    case "date":
      return (
        <Input
          id={`aspect-${aspect.id}`}
          type="date"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
    case "select":
      return (
        <Select
          value={typeof value === "string" ? value : ""}
          onValueChange={(v) => onChange(v)}
        >
          <SelectTrigger id={`aspect-${aspect.id}`}>
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
        />
      );
  }
}

export function AspectsForm({
  answers,
  onChange,
  excludeIds = [],
}: {
  answers: AspectAnswers;
  onChange: (answers: AspectAnswers) => void;
  /** Aspects already covered by the personal-details section (FR-5.1). */
  excludeIds?: number[];
}) {
  const visible = ASPECTS.filter((a) => !excludeIds.includes(a.id));
  const categories = Array.from(new Set(visible.map((a) => a.category)));

  return (
    <div className="space-y-8">
      {categories.map((category) => (
        <div key={category} className="space-y-4">
          <h3 className="border-b pb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {category}
          </h3>
          {visible
            .filter((a) => a.category === category)
            .map((aspect) => (
              <div key={aspect.id} className="space-y-2">
                <Label htmlFor={`aspect-${aspect.id}`}>
                  <span className="mr-1.5 text-muted-foreground">
                    {aspect.id}.
                  </span>
                  {aspect.question}
                </Label>
                <AspectControl
                  aspect={aspect}
                  value={answers[aspect.id]}
                  onChange={(v) => onChange({ ...answers, [aspect.id]: v })}
                />
              </div>
            ))}
        </div>
      ))}
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
  return (
    <div className="space-y-6">
      {Array.from(new Set(ASPECTS.map((a) => a.category))).map((category) => {
        const rows = ASPECTS.filter((a) => a.category === category);
        return (
          <div key={category}>
            <h3 className="mb-2 border-b pb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              {category}
            </h3>
            <dl className="space-y-2">
              {rows.map((def) => {
                const resp = byId.get(def.id);
                const answer = resp?.answer;
                let display: string;
                if (answer === null || answer === undefined || answer === "") {
                  display = "—";
                } else if (typeof answer === "boolean") {
                  display = answer ? "Yes" : "No";
                } else {
                  display = String(answer);
                }
                return (
                  <div
                    key={def.id}
                    className="grid grid-cols-1 gap-1 rounded-md border p-3 sm:grid-cols-2"
                  >
                    <dt className="text-sm text-muted-foreground">
                      <span className="mr-1.5">{def.id}.</span>
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
    </div>
  );
}

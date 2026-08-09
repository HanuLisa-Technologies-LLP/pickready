"use client";

// The small cells shared by the BD Reach table and by
// their stacked-card layout under `md`. Kept here so the table and the cards
// render the same thing rather than two drifting copies.

import * as React from "react";
import { Check, X } from "lucide-react";

import {
  SOCIAL_SOURCE_LABELS,
  type BDProgressStep,
  type BDSocialSource,
} from "@/lib/bd-types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** "3 Aug 2026". Returns null for an absent stamp so callers can branch. */
export function formatStamp(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function SourceBadge({ source }: { source: BDSocialSource | null }) {
  // NULL is not "unknown": a personal lead cannot carry a source, by database
  // constraint. On the merged BD Reach table that column is always shown, so it
  // has to say what the absence MEANS rather than reading as missing data.
  if (!source) return <span className="text-sm">Approached directly</span>;
  return <Badge variant="brand">{SOCIAL_SOURCE_LABELS[source]}</Badge>;
}

/**
 * One of the six progress boxes.
 *
 * The `_at` stamp is surfaced, not hidden: a rep looking at a ticked box wants
 * to know WHEN it happened. In the table it is a tooltip (six date captions per
 * row would drown the row), in the stacked cards it is printed beside the label.
 */
export function ProgressCheckbox({
  step,
  leadName,
  disabled,
  showCaption = false,
  onToggle,
}: {
  step: BDProgressStep;
  leadName: string;
  disabled?: boolean;
  /** Print the date beside the label instead of only in a tooltip. */
  showCaption?: boolean;
  onToggle: (next: boolean) => void;
}) {
  const stamp = formatStamp(step.at);
  const description = step.done
    ? stamp
      ? `${step.label} done, first marked ${stamp}`
      : `${step.label} done`
    : stamp
      ? `${step.label} not done, previously marked ${stamp}`
      : `${step.label} not done yet`;

  const box = (
    <Checkbox
      checked={step.done}
      disabled={disabled}
      aria-label={`${step.label} for ${leadName}`}
      onCheckedChange={(checked) => onToggle(checked === true)}
    />
  );

  if (showCaption) {
    return (
      <label className="flex items-center gap-2 text-sm">
        {box}
        <span>{step.label}</span>
        {stamp ? <span className="text-xs">({stamp})</span> : null}
      </label>
    );
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{box}</span>
      </TooltipTrigger>
      <TooltipContent>{description}</TooltipContent>
    </Tooltip>
  );
}

export type AgreementChoice = true | false | null;

/**
 * The final stage: yes, no, or back to undecided.
 *
 * Three-valued on purpose. "Nobody has decided" and "they declined" are
 * genuinely different states, and collapsing them would quietly mark every
 * fresh lead as lost. Every change routes through the caller's confirmation,
 * because a yes CREATES a customer and taking a yes away ARCHIVES one.
 */
export function AgreementCell({
  agreement,
  agreementAt,
  disabled,
  leadName,
  onChoose,
}: {
  agreement: boolean | null;
  agreementAt: string | null;
  disabled?: boolean;
  leadName: string;
  onChoose: (choice: AgreementChoice) => void;
}) {
  const stamp = formatStamp(agreementAt);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button
        type="button"
        size="sm"
        variant={agreement === true ? "default" : "outline"}
        disabled={disabled}
        aria-pressed={agreement === true}
        aria-label={`Mark the agreement with ${leadName} as signed`}
        className="h-8 gap-1 px-2.5"
        onClick={() => onChoose(true)}
      >
        <Check className="h-3.5 w-3.5" /> Yes
      </Button>
      <Button
        type="button"
        size="sm"
        variant={agreement === false ? "default" : "outline"}
        disabled={disabled}
        aria-pressed={agreement === false}
        aria-label={`Mark the agreement with ${leadName} as declined`}
        className="h-8 gap-1 px-2.5"
        onClick={() => onChoose(false)}
      >
        <X className="h-3.5 w-3.5" /> No
      </Button>
      {agreement !== null ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={disabled}
          aria-label={`Set the agreement with ${leadName} back to undecided`}
          className="h-8 px-2 text-xs"
          onClick={() => onChoose(null)}
        >
          Clear
        </Button>
      ) : null}
      {agreement === true && stamp ? (
        <span className="w-full text-xs">Signed {stamp}</span>
      ) : null}
    </div>
  );
}

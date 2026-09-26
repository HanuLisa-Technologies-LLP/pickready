"use client";

/**
 * The eight columns' cells. One component per column, in the specified order.
 *
 * Split out of the table so each cell's rule is testable on its own: AI
 * Match's muted styling rule, the Vivekium Grade's gradeless and under-review
 * states, and the Note's truncation are each a property of one component
 * rather than of a row. No cell renders a number: every word is the server's.
 */

import * as React from "react";
import { Copy, Lock, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import {
  AI_MATCH_CLASS,
  CONFIDENCE_DOT_CLASS,
  GRADE_CLASS,
  STATES_WITHOUT_A_GRADE,
  STATE_NOT_CHECKED,
  STATE_UNDER_REVIEW,
} from "./grade";
import type { DashboardRow } from "./types";

/* ── Column 1: Candidate ─────────────────────────────────────────────────── */

export function CandidateCell({ row }: { row: DashboardRow }) {
  const [copied, setCopied] = React.useState(false);

  const copy = React.useCallback(() => {
    // `navigator.clipboard` is absent in an insecure context and in jsdom. The
    // affordance simply does not confirm rather than throwing into a click
    // handler, and the code stays selectable with the keyboard either way.
    void navigator.clipboard?.writeText(row.system_id).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => setCopied(false)
    );
  }, [row.system_id]);

  return (
    <div className="min-w-0">
      <p className="truncate text-[13.5px] font-bold leading-5">{row.full_name}</p>
      <span className="group inline-flex items-center gap-1">
        <span className="select-all font-mono text-[11px] leading-4">
          {row.system_id}
        </span>
        <button
          type="button"
          onClick={copy}
          // Visible on hover AND on keyboard focus. A control that appears only
          // on hover is a control a keyboard user does not have.
          className="opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
          aria-label={`Copy candidate code ${row.system_id}`}
        >
          <Copy className="h-3 w-3" aria-hidden="true" />
        </button>
        <span className="sr-only" role="status">
          {copied ? "Candidate code copied" : ""}
        </span>
      </span>
      <p className="truncate text-[11px] leading-4">
        {row.job_title}
      </p>
    </div>
  );
}

/* ── Column 2: Source ────────────────────────────────────────────────────── */

export function SourceCell({ row }: { row: DashboardRow }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] font-normal text-foreground">
      {row.source_label}
    </span>
  );
}

/* ── Column 3: AI Match ──────────────────────────────────────────────────── */

/**
 * MUTED / OUTLINE ONLY. Never a solid fill, never a brand colour, never bold.
 *
 * Yukti's reading of the resume alone: an early signal, not a verdict. See
 * `grade.ts` for why. The class list is a single constant so there is one
 * place to read it and one place a test can check it.
 */
export function AiMatchCell({ row }: { row: DashboardRow }) {
  const graded = !STATES_WITHOUT_A_GRADE.has(row.ai_match_state);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="ai-match"
          data-state={row.ai_match_state}
          className={cn(AI_MATCH_CLASS, !graded && "border-dashed")}
        >
          <span aria-hidden="true">{row.ai_match_label}</span>
          {/* The whole meaning, spoken: the word, that it is the resume only,
              and why when there is no word. */}
          <span className="sr-only">{row.ai_match_screen_reader_label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{row.ai_match_note}</TooltipContent>
    </Tooltip>
  );
}

/* ── Column 4: Vivekium Grade ────────────────────────────────────────────── */

export function ReadyPickGradeCell({ row }: { row: DashboardRow }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="ready-pick-grade"
          data-state={row.ranking_state}
          className={cn(
            "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1",
            GRADE_CLASS[row.ranking_state] ?? GRADE_CLASS[STATE_NOT_CHECKED]
          )}
        >
          {row.ranking_state === STATE_UNDER_REVIEW ? (
            <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : null}
          <span aria-hidden="true" className="truncate text-[12px] font-bold leading-4">
            {row.ranking_label}
          </span>
          <span
            aria-hidden="true"
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              CONFIDENCE_DOT_CLASS[row.confidence_indicator]
            )}
          />
          {/* The whole meaning, spoken. Colour and a dot carry none of it. */}
          <span className="sr-only">
            {row.ranking_screen_reader_label} {row.confidence_label}.
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs space-y-1">
        <p className="font-semibold">{row.ranking_label}</p>
        <p>{row.ranking_note}</p>
        <p>{row.confidence_label}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/* ── Column 5: Vivekium Note ───────────────────────────────────────────── */

export function NoteCell({ row }: { row: DashboardRow }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <p
          data-testid="ready-pick-note"
          className={cn(
            "max-w-[210px] truncate text-[12px] font-normal leading-5 text-foreground",
            // Never bold, never coloured. Colour is column 4's, and spending it
            // here would take the meaning out of the one place it means
            // something.
            row.note_is_pending && "italic"
          )}
        >
          {row.note}
        </p>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">{row.note}</TooltipContent>
    </Tooltip>
  );
}

/* ── Column 6: Vivekium Profile ────────────────────────────────────────── */

export function ProfileButton({
  row,
  onOpen,
}: {
  row: DashboardRow;
  onOpen: (row: DashboardRow) => void;
}) {
  const available = row.profile !== null;
  const button = (
    <Button
      type="button"
      size="sm"
      variant="default"
      disabled={!available}
      onClick={() => onOpen(row)}
      className={cn("h-8", !available && "cursor-not-allowed")}
      aria-label={
        available
          ? `Open the Vivekium Profile for ${row.full_name}`
          : `Vivekium Profile not available for ${row.full_name}`
      }
    >
      {available ? "Vivekium Profile" : "Awaiting Profile"}
    </Button>
  );
  if (available) return button;
  return (
    <Tooltip>
      {/* A disabled button fires no pointer events, so the tooltip needs a
          wrapper to hang off. Without it the explanation is unreachable, which
          is the state a person most needs it in. */}
      <TooltipTrigger asChild>
        <span tabIndex={0}>{button}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        {row.profile_pending_reason}
      </TooltipContent>
    </Tooltip>
  );
}

/* ── Column 7: Team Review ───────────────────────────────────────────────── */

export function TeamReviewButton({
  row,
  onOpen,
  disabledReason,
}: {
  row: DashboardRow;
  onOpen: (row: DashboardRow) => void;
  disabledReason: string | null;
}) {
  // SECONDARY styling, and deliberately NOT teal. The Dashboard document
  // suggests "teal vs primary blue"; in this design system teal means
  // CORROBORATED EVIDENCE, and a person's opinion is the furthest thing from
  // it. Spending the evidence colour on the subjective column would empty it
  // of meaning everywhere else.
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => onOpen(row)}
      className="h-8"
      aria-label={`Open Team Review for ${row.full_name}`}
      title={disabledReason ?? undefined}
    >
      Team Review
      {row.team_review_count > 0 ? (
        <span className="ml-1.5 rounded-full bg-muted px-1.5 text-[11px] text-foreground">
          {row.team_review_count}
        </span>
      ) : null}
    </Button>
  );
}

/* ── Column 8: Stage ─────────────────────────────────────────────────────── */

export function StageCell({
  row,
  canMove,
  disabledReason,
  onOpen,
}: {
  row: DashboardRow;
  canMove: boolean;
  disabledReason: string | null;
  onOpen: (row: DashboardRow) => void;
}) {
  const locked = row.under_integrity_review || !canMove;
  const reason = row.under_integrity_review
    ? "Pending integrity review, HR Manager only"
    : disabledReason;

  const control = (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      disabled={locked}
      onClick={() => onOpen(row)}
      className={cn(
        "h-8 justify-start gap-1.5 px-2",
        locked && "cursor-not-allowed opacity-40"
      )}
      aria-label={`Move ${row.full_name} from ${row.stage_label}`}
    >
      {locked ? <Lock className="h-3.5 w-3.5" aria-hidden="true" /> : null}
      <span className="text-[12px]">{row.stage_label}</span>
    </Button>
  );

  if (!locked) return control;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0}>{control}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{reason}</TooltipContent>
    </Tooltip>
  );
}

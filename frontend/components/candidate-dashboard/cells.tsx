"use client";

/**
 * The eight columns' cells. One component per column, in the specified order.
 *
 * Split out of the table so each cell's rule is testable on its own: the
 * Pre-Screen Grade's styling rule, the Ready Pick Score's pending and
 * under-review states, and the Note's truncation are each a property of one
 * component rather than of a row.
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
  BANDS_WITHOUT_A_SCORE,
  BAND_CLASS,
  BAND_PENDING,
  BAND_UNDER_REVIEW,
  CONFIDENCE_DOT_CLASS,
  PRE_SCREEN_CLASS,
} from "./band";
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
        <span className="select-all font-mono text-[11px] leading-4 text-foreground/80">
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
      <p className="truncate text-[11px] leading-4 text-foreground/80">
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

/* ── Column 3: Pre-Screen Grade ──────────────────────────────────────────── */

/**
 * MUTED / OUTLINE ONLY. Never a solid fill, never a brand colour, never bold.
 *
 * See `band.ts` for why. The class list is a single constant so there is one
 * place to read it and one place a test can check it.
 */
export function PreScreenGradeCell({ row }: { row: DashboardRow }) {
  const graded = row.pre_screen_grade !== null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="pre-screen-grade"
          data-graded={graded ? "true" : "false"}
          className={cn(PRE_SCREEN_CLASS, !graded && "border-dashed")}
        >
          {/* An ungraded row says so in words. It is NOT rendered as `Hold`,
              which is a graded outcome meaning a person should look. */}
          {graded ? row.pre_screen_grade : "Not graded"}
          <span className="sr-only"> {row.pre_screen_label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{row.pre_screen_label}</TooltipContent>
    </Tooltip>
  );
}

/* ── Column 4: Ready Pick Score ──────────────────────────────────────────── */

export function ReadyPickScoreCell({ row }: { row: DashboardRow }) {
  const scoreless = BANDS_WITHOUT_A_SCORE.has(row.band);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="ready-pick-score"
          data-band={row.band}
          className={cn(
            "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1",
            BAND_CLASS[row.band] ?? BAND_CLASS[BAND_PENDING]
          )}
        >
          {row.band === BAND_UNDER_REVIEW ? (
            <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : null}
          <span className="font-mono text-[15px] font-bold leading-5">
            {/* The Dashboard specification draws this placeholder as an em
                dash. An em dash (U+2014) is forbidden in every string in this
                product, so it is rendered as an en dash (U+2013) instead. The
                forbidden character is deliberately not written in this comment:
                the repo-wide sweep reads source, not intent. */}
            {row.ready_pick_score === null ? "–" : row.ready_pick_score}
          </span>
          <span className="truncate text-[11px] font-bold leading-4">
            {row.band_label}
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
            {row.band_screen_reader_label}. {row.confidence_label}.
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs space-y-1">
        <p className="font-semibold">{row.band_label}</p>
        <p>{row.confidence_label}</p>
        {/* No fabricated interval. The specification asks for `82 [76 to 88]`
            and nothing in the engine publishes one; a bracket invented here
            would be a number with no provenance beside one that has some. */}
        <p>{scoreless ? row.band_screen_reader_label : row.score_range_note}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/* ── Column 5: Ready Pick Note ───────────────────────────────────────────── */

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

/* ── Column 6: Ready Pick Profile ────────────────────────────────────────── */

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
          ? `Open the Ready Pick Profile for ${row.full_name}`
          : `Ready Pick Profile not available for ${row.full_name}`
      }
    >
      {available ? "Ready Pick Profile" : "Awaiting Profile"}
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

"use client";

/**
 * The Candidate Dashboard: the client's daily working surface.
 *
 * Eight columns, in the specification's scanning order, over every candidate
 * the caller may see. Backend complexity stays in the dossier; the table is a
 * fast triage and decision tool.
 *
 * WHAT THIS COMPONENT DELIBERATELY DOES NOT DO
 * ---------------------------------------------
 *   * It does not SORT. Order comes from the API, which sorts in SQL with a
 *     total order. Re-sorting one page here would let a candidate appear on
 *     two pages, or on none, as scores change.
 *   * It does not FILTER. Same reason: filtering a fetched page makes the match
 *     count depend on which page happened to be loaded.
 *   * It does not compute a band, a grade or a label from a number. Every word
 *     on screen was chosen by the server, so a provider outage cannot produce a
 *     rendering layer's own opinion of a candidate.
 *   * It does not decide who may do what. `controls` is resolved server-side
 *     and every control is refused again at its own route (RBAC 3).
 *
 * MOBILE
 * ------
 * Columns 6 to 8 stack into a vertical group at the end of the row, the
 * Pre-Screen Grade and Note collapse into the candidate cell, and column 4
 * never collapses. Horizontal scroll is acceptable and lives on the table's own
 * container, so the PAGE never scrolls sideways.
 */

import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { apiGet } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  CandidateCell,
  NoteCell,
  PreScreenGradeCell,
  ProfileButton,
  ReadyPickScoreCell,
  SourceCell,
  StageCell,
  TeamReviewButton,
} from "./cells";
import { ReadyPickProfilePanel, StageSheet, TeamReviewSheet } from "./panels";
import type { DashboardPage, DashboardRow } from "./types";

const BASE = "/dashboard";

/** Header labels, in `dashboard.COLUMNS` order. Column 1 reads "Candidate";
 *  the SPOKEN header is "Candidate Code Name" and comes from the server. */
const HEADERS: Array<[key: string, label: string, className: string]> = [
  ["candidate", "Candidate", "min-w-[220px]"],
  ["source", "Source", "hidden md:table-cell"],
  ["pre_screen_grade", "Pre-Screen", "hidden md:table-cell"],
  ["ready_pick_score", "Ready Pick Score", "min-w-[210px]"],
  ["ready_pick_note", "Ready Pick Note", "hidden lg:table-cell"],
  ["ready_pick_profile", "Profile", ""],
  ["team_review", "Team Review", ""],
  ["stage", "Stage", ""],
];

export interface CandidateDashboardProps {
  /** Optional single-job view. Absent means every job the caller may see. */
  jobId?: string;
}

export function CandidateDashboard({ jobId }: CandidateDashboardProps) {
  const [page, setPage] = React.useState(1);
  const [sort, setSort] = React.useState("score");
  const [direction, setDirection] = React.useState("desc");
  const [sourceType, setSourceType] = React.useState<string | null>(null);
  const [grade, setGrade] = React.useState<string | null>(null);
  const [data, setData] = React.useState<DashboardPage | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const [profileRow, setProfileRow] = React.useState<DashboardRow | null>(null);
  const [reviewRow, setReviewRow] = React.useState<DashboardRow | null>(null);
  const [stageRow, setStageRow] = React.useState<DashboardRow | null>(null);

  const load = React.useCallback(() => {
    const params = new URLSearchParams({
      page: String(page),
      sort,
      direction,
    });
    if (jobId) params.set("job_id", jobId);
    if (sourceType) params.set("source_type", sourceType);
    if (grade) params.set("pre_screen_grade", grade);
    setLoading(true);
    setError(null);
    apiGet<DashboardPage>(`${BASE}/candidates?${params.toString()}`)
      .then(setData)
      .catch((cause) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "The candidate list could not be loaded."
        )
      )
      .finally(() => setLoading(false));
  }, [page, sort, direction, sourceType, grade, jobId]);

  React.useEffect(() => {
    load();
  }, [load]);

  // `/` then a candidate code jumps to that candidate's profile, which is the
  // specification's keyboard shortcut. Implemented over the LOADED page only,
  // and it says so when the code is not on this page rather than silently
  // doing nothing.
  const [jumpOpen, setJumpOpen] = React.useState(false);
  const [jumpValue, setJumpValue] = React.useState("");
  const [jumpMessage, setJumpMessage] = React.useState("");
  const jumpRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        setJumpOpen(true);
        setJumpMessage("");
        window.setTimeout(() => jumpRef.current?.focus(), 0);
      }
      if (event.key === "Escape") {
        setJumpOpen(false);
        setProfileRow(null);
        setReviewRow(null);
        setStageRow(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const jump = () => {
    const wanted = jumpValue.trim().toUpperCase();
    const found = data?.rows.find((row) => row.system_id === wanted);
    if (!found) {
      setJumpMessage(`No candidate on this page carries the code ${wanted}.`);
      return;
    }
    if (!found.profile) {
      setJumpMessage(found.profile_pending_reason ?? "No profile yet.");
      return;
    }
    setJumpOpen(false);
    setJumpValue("");
    setProfileRow(found);
  };

  const controls = data?.controls;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs font-semibold uppercase tracking-wide">
            Sort by
            <select
              className="ml-2 h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={`${sort}:${direction}`}
              onChange={(event) => {
                const [nextSort, nextDirection] = event.target.value.split(":");
                setSort(nextSort);
                setDirection(nextDirection);
                setPage(1);
              }}
            >
              <option value="score:desc">Ready Pick Score, highest first</option>
              <option value="score:asc">Ready Pick Score, lowest first</option>
              <option value="name:asc">Name</option>
              <option value="added:desc">Date added, newest first</option>
              <option value="source:asc">Source</option>
              <option value="pre_screen:asc">Pre-Screen Grade</option>
              <option value="stage:asc">Stage</option>
            </select>
          </label>

          <label className="text-xs font-semibold uppercase tracking-wide">
            Source
            <select
              className="ml-2 h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={sourceType ?? ""}
              onChange={(event) => {
                setSourceType(event.target.value || null);
                setPage(1);
              }}
            >
              <option value="">All sources</option>
              {/* Served by the API, never hardcoded here. A two-value filter
                  would silently hide every `sourced` candidate. */}
              {(data?.source_types ?? []).map((value) => (
                <option key={value} value={value}>
                  {data?.source_labels?.[value] ?? value}
                </option>
              ))}
            </select>
          </label>

          <label className="text-xs font-semibold uppercase tracking-wide">
            Pre-Screen
            <select
              className="ml-2 h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={grade ?? ""}
              onChange={(event) => {
                setGrade(event.target.value || null);
                setPage(1);
              }}
            >
              <option value="">All grades</option>
              {(data?.pre_screen_grades ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          {controls?.scoped_to_assignments ? (
            <p className="ml-auto text-[12px]">
              Showing the jobs you are assigned to.
            </p>
          ) : null}
        </div>

        {jumpOpen ? (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border p-3">
            <label className="text-sm">
              Jump to candidate code
              <input
                ref={jumpRef}
                className="ml-2 h-9 rounded-md border border-input bg-background px-2 font-mono text-sm"
                value={jumpValue}
                onChange={(event) => setJumpValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") jump();
                }}
              />
            </label>
            <Button type="button" size="sm" onClick={jump}>
              Open profile
            </Button>
            {jumpMessage ? (
              <p className="text-sm" role="status">
                {jumpMessage}
              </p>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <p className="rounded-xl border border-dashed p-5 text-sm" role="alert">
            {error}
          </p>
        ) : null}

        {/* The horizontal scroll lives HERE, on the table's own container, so a
            wide table never makes the page scroll sideways. */}
        <div className="overflow-x-auto rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                {HEADERS.map(([key, label, className]) => (
                  <TableHead
                    key={key}
                    className={cn(
                      "text-[10.5px] uppercase tracking-wider",
                      className
                    )}
                  >
                    <span aria-hidden="true">{label}</span>
                    <span className="sr-only">
                      {data?.column_labels?.[key] ?? label}
                    </span>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data
                ? Array.from({ length: 5 }).map((_, index) => (
                    <TableRow key={index}>
                      <TableCell colSpan={HEADERS.length}>
                        <Skeleton className="h-10 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                : null}

              {data?.rows.map((row) => (
                <TableRow
                  key={row.link_id}
                  data-testid="dashboard-row"
                  data-under-review={row.under_integrity_review ? "true" : "false"}
                  className={cn(
                    "h-[60px]",
                    // The row state, and it is never carried by colour alone:
                    // column 4 also reads "Under Review" and the stage control
                    // shows a lock with a tooltip.
                    row.under_integrity_review && "border-l-4 border-l-warning",
                    row.archived && "opacity-50"
                  )}
                >
                  <TableCell className="px-3.5">
                    <CandidateCell row={row} />
                    {/* On a narrow viewport the two supporting columns collapse
                        into this cell rather than being hidden. */}
                    <div className="mt-1 flex items-center gap-2 md:hidden">
                      <PreScreenGradeCell row={row} />
                      <SourceCell row={row} />
                    </div>
                  </TableCell>
                  <TableCell className="hidden px-3.5 md:table-cell">
                    <SourceCell row={row} />
                  </TableCell>
                  <TableCell className="hidden px-3.5 md:table-cell">
                    <PreScreenGradeCell row={row} />
                  </TableCell>
                  <TableCell className="px-3.5">
                    <ReadyPickScoreCell row={row} />
                  </TableCell>
                  <TableCell className="hidden px-3.5 lg:table-cell">
                    <NoteCell row={row} />
                  </TableCell>
                  <TableCell className="px-3.5">
                    <ProfileButton row={row} onOpen={setProfileRow} />
                  </TableCell>
                  <TableCell className="px-3.5">
                    <TeamReviewButton
                      row={row}
                      onOpen={setReviewRow}
                      disabledReason={controls?.team_review_disabled_reason ?? null}
                    />
                  </TableCell>
                  <TableCell className="px-3.5">
                    <StageCell
                      row={row}
                      canMove={controls?.can_move_stage ?? false}
                      disabledReason={controls?.stage_disabled_reason ?? null}
                      onOpen={setStageRow}
                    />
                  </TableCell>
                </TableRow>
              ))}

              {data && !data.rows.length && !loading ? (
                <TableRow>
                  <TableCell colSpan={HEADERS.length} className="p-8 text-center text-sm">
                    No candidates match this view.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>

        <div className="flex items-center justify-between text-sm">
          <p>
            {data ? `${data.total} candidates` : ""}
          </p>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              aria-label="Previous page"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <span>
              Page {page} of {totalPages}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={page >= totalPages}
              onClick={() => setPage((current) => current + 1)}
              aria-label="Next page"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        <ReadyPickProfilePanel
          row={profileRow}
          open={profileRow !== null}
          onOpenChange={(open) => !open && setProfileRow(null)}
        />
        <TeamReviewSheet
          row={reviewRow}
          open={reviewRow !== null}
          onOpenChange={(open) => !open && setReviewRow(null)}
          onSaved={load}
        />
        <StageSheet
          row={stageRow}
          open={stageRow !== null}
          onOpenChange={(open) => !open && setStageRow(null)}
          onMoved={load}
          canDisposition={controls?.can_disposition_integrity ?? false}
        />
      </div>
    </TooltipProvider>
  );
}

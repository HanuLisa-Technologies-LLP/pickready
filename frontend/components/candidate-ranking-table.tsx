"use client";

// The inline candidate ranking table (spec §2). Lives ON the job page, there
// is no separate review screen any more.
//
// Columns (new spec, 2026-07-28):
//   Name | Type of Procurement | Status | Resume | AI Rating & Report |
//   PPI Report | Decision
//
// Changes the client asked for on 2026-07-28:
//   * Level column removed. The grade is a property of the JOB, so printing it
//     on every candidate row repeated one value down the whole table.
//   * "Rating & comments" became "AI Rating & Report": the cell now shows the
//     Overall tag only, behind an AI Report button that opens the full
//     reasoning in a large document view. The five comments used to be printed
//     inline, which made rows tall enough to see only about four candidates.
//   * Type of Procurement added, so a recruiter can tell an applicant from a
//     sourced or bulk-uploaded one. ASSUMPTION: the spec gives the column
//     order as Name, Status, Resume, AI Rating, PPI, Decision and separately
//     asks for a procurement column without placing it. It sits right after
//     Name, where a "who is this and where did they come from" pair reads
//     naturally and stays visible when the table scrolls sideways.
//
// Two rules this component exists to honour:
//   * NO NUMBERS. Every rating is a word label supplied by the backend; this
//     file contains no score, percentage, or rank arithmetic at all.
//   * NO CLIENT SORT. Order comes from the API, which sorts in SQL by the
//     job's grade with a total order. Re-sorting a single page here would let a
//     candidate appear on two pages, or on none, as scores change.

import * as React from "react";
import {
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  FileText,
  Mail,
  MessageSquareText,
  MessagesSquare,
} from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type {
  ProfileAge,
  RankedCandidate,
  RankedCandidatesResponse,
  ReviewProfileResponse,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { BandLegend } from "@/components/rating-label";
import { AiRatingCell, AiRatingReportModal } from "@/components/ai-rating-report-modal";
import { ProcurementBadge } from "@/components/procurement-badge";
import { StageBadge, StatusActions } from "@/components/pipeline-status";
import { TierBadge } from "@/components/tier-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CandidateTeamReviewModal } from "@/components/candidate-team-review-modal";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface CandidateRankingTableHandle {
  reload: () => void;
}

function ValidationAnswersModal({
  row,
  open,
  onOpenChange,
}: {
  row: RankedCandidate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const answers = row?.validation_answers ?? [];
  // Grouped by the application's own six fields plus the profile form's own
  // sections, so 44 answers read as two questionnaires rather than one flat
  // wall of text. Order follows first appearance, which is already form order
  // (the server assembles it that way).
  const groups: Array<{ title: string; items: typeof answers }> = [];
  for (const item of answers) {
    const title = item.group ?? "Application";
    let group = groups.find((g) => g.title === title);
    if (!group) {
      group = { title, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Validation Q&amp;A</DialogTitle>
          <DialogDescription>
            {row?.full_name ?? "Candidate"}&apos;s application and profile answers, shown exactly as submitted and without AI scoring.
          </DialogDescription>
        </DialogHeader>
        {answers.length ? (
          <div className="space-y-5">
            {groups.map((group) => (
              <div key={group.title}>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.title}
                </h4>
                <dl className="divide-y rounded-xl border">
                  {group.items.map((item) => (
                    <div key={item.key} className="p-4">
                      <dt className="text-sm font-semibold">{item.question}</dt>
                      <dd className="mt-2 whitespace-pre-wrap text-sm leading-6">
                        {item.answer?.trim() || "Not answered"}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded-xl border border-dashed p-5 text-sm">
            No validation answers were recorded for this application.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function CandidateRankingTable({
  jobId,
  onOpenReport,
  onOpenTranscript,
  onOpenResume,
  onEmail,
  onSelectionChange,
  canDecide = false,
  reloadKey = 0,
}: {
  jobId: string;
  onOpenReport: (row: RankedCandidate) => void;
  /**
   * Open the question-and-answer transcript. Separate from the report on
   * purpose, and available EARLIER than it: the transcript exists the moment
   * the candidate answers question one, while the report does not exist until
   * they finish. A recruiter chasing a stalled assessment needs the former.
   */
  onOpenTranscript: (row: RankedCandidate) => void;
  onOpenResume: (row: RankedCandidate) => void;
  /** Omit to hide the email action (caller lacks send_outreach). */
  onEmail?: (rows: RankedCandidate[]) => void;
  /** Show the status-action column (caller has decide_profile). */
  canDecide?: boolean;
  /** Notified whenever the tick-box selection changes, so the job page can
   *  offer "Send Assessment Invitations" for the selected rows. */
  onSelectionChange?: (rows: RankedCandidate[]) => void;
  /** Bump to force a refetch, e.g. after a matching run finishes. */
  reloadKey?: number;
}) {
  const { toast } = useToast();
  const [page, setPage] = React.useState(1);
  // Old Profiles vs New Profiles (spec §4.2). Only meaningful once a job has
  // been renewed; the tabs render regardless so the distinction is discoverable
  // rather than appearing out of nowhere the day a posting is renewed.
  const [profileAge, setProfileAge] = React.useState<ProfileAge | "all">("all");
  const [data, setData] = React.useState<RankedCandidatesResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  // The row whose full AI report is open. Null closes the dialog.
  const [aiReportRow, setAiReportRow] = React.useState<RankedCandidate | null>(null);
  const [teamReviewRow, setTeamReviewRow] = React.useState<RankedCandidate | null>(null);
  const [validationRow, setValidationRow] = React.useState<RankedCandidate | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ page: String(page) });
      if (profileAge !== "all") query.set("profile_age", profileAge);
      const res = await apiGet<RankedCandidatesResponse>(
        `/jobs/${jobId}/candidates?${query.toString()}`
      );
      setData(res);
    } catch (e) {
      toast({
        title: "Couldn't load candidates",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [jobId, page, profileAge, toast]);

  React.useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // Selection is per-page on purpose: "select all" across pages you have not
  // read would let a recruiter email people they never looked at.
  React.useEffect(() => setSelected(new Set()), [page, profileAge, reloadKey]);
  // Changing the filter changes what page 1 even means, so go back to it.
  React.useEffect(() => setPage(1), [profileAge]);

  const rows = data?.results ?? [];
  const toggle = (linkId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(linkId)) next.delete(linkId);
      else next.add(linkId);
      return next;
    });

  const allOnPageSelected = rows.length > 0 && rows.every((r) => selected.has(r.link_id));
  // Keep the empty-state cell spanning the WHOLE table as columns come and go
  // with the caller's capabilities, a hardcoded span leaves a ragged row.
  const selectable = Boolean(onEmail || onSelectionChange);
  const columnCount = 8 + (selectable ? 1 : 0) + (canDecide ? 2 : 0);
  const selectedRows = rows.filter((r) => selected.has(r.link_id));

  /**
   * Open a candidate's detail view, recording the review if it is an Old
   * Profile (spec §3.2).
   *
   * The charge is fire-and-forget on purpose. It is a twentieth of a credit,
   * it is idempotent per (profile, reviewer) server-side, and a recruiter must
   * never be blocked from reading a candidate because a billing write was slow
   * or failed. If it does fail, the profile simply was not billed, which is the
   * right way round for that error to land.
   */
  const openCandidateDetail = React.useCallback(
    (row: RankedCandidate) => {
      setAiReportRow(row);
      if (row.profile_age !== "old" || row.review_charged) return;
      void apiPost<ReviewProfileResponse>(
        `/jobs/${jobId}/candidates/${row.link_id}/review`
      )
        .then(() => load())
        .catch(() => {
          /* Reading is never gated on the charge. See above. */
        });
    },
    [jobId, load]
  );

  // Report the selection upward so the job page can act on it (assessment
  // invitations). Keyed on the id set rather than the row objects, which are
  // new on every fetch and would loop.
  const selectedKey = selectedRows.map((r) => r.link_id).join(",");
  React.useEffect(() => {
    onSelectionChange?.(selectedRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);

  return (
    <section aria-label="Candidate ranking">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          {data && data.total > 0 ? (
            <>
              Showing {data.range_start}–{data.range_end} of {data.total} candidate
              {data.total === 1 ? "" : "s"}
            </>
          ) : loading ? (
            "Loading candidates"
          ) : (
            "No candidates have applied to this job yet."
          )}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {/* Old Profiles / New Profiles. Filtering only: an Old Profile is
              ranked, opened and assessed exactly like a new one, so this
              narrows the view and never restricts what can be done with a row. */}
          <div
            role="group"
            aria-label="Filter by when the candidate applied"
            className="inline-flex rounded-lg border border-border p-0.5"
          >
            {(
              [
                ["all", "All"],
                ["new", "New Profiles"],
                ["old", "Old Profiles"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={profileAge === value}
                onClick={() => setProfileAge(value)}
                className={
                  "rounded-md px-3 py-1.5 text-xs font-medium transition-colors " +
                  (profileAge === value
                    ? "bg-brand-100 text-accent-foreground"
                    : "hover:bg-brand-100/60")
                }
              >
                {label}
              </button>
            ))}
          </div>
          {onEmail && selectedRows.length > 0 ? (
            <Button size="sm" className="gap-1.5" onClick={() => onEmail(selectedRows)}>
              <Mail className="h-4 w-4" />
              Email {selectedRows.length} selected
            </Button>
          ) : null}
        </div>
      </div>
      {profileAge === "old" ? (
        <p className="mb-3 text-sm leading-6">
          These people applied before this job was renewed. Their profiles stay
          yours to read, and opening one for the first time draws a twentieth of
          a credit from your pool.
        </p>
      ) : null}

      {/* Horizontal scroll on narrow screens (spec §10), the page body itself
          must never scroll sideways. */}
      <div className="overflow-x-auto rounded-lg border">
        <Table className="min-w-[900px]">
          <TableHeader>
            <TableRow>
              {selectable ? (
                <TableHead className="w-10">
                  <input
                    type="checkbox"
                    aria-label="Select all candidates on this page"
                    className="h-4 w-4 accent-foreground"
                    checked={allOnPageSelected}
                    onChange={() =>
                      setSelected(
                        allOnPageSelected
                          ? new Set()
                          : new Set(rows.map((r) => r.link_id))
                      )
                    }
                  />
                </TableHead>
              ) : null}
              <TableHead className="w-[200px]">Name</TableHead>
              <TableHead className="w-[130px]">Type of Procurement</TableHead>
              <TableHead className="w-[150px]">Status</TableHead>
              <TableHead className="w-[110px]">Resume</TableHead>
              <TableHead className="w-[180px]">
                AI Rating &amp; Report
                {/* The same line the modal carries, so the column cannot be
                    read as an assessed verdict. This rating is written from the
                    resume and the JD alone. */}
                <span className="mt-0.5 block text-[11px] font-normal">
                  Based on Candidate Resume and JD
                </span>
              </TableHead>
              <TableHead className="w-[110px]">PPI Report</TableHead>
              <TableHead className="w-[110px]">Q&amp;A</TableHead>
              {/* The validation questionnaire, as its own column (spec 29).
                  Separate from Q&A on purpose: that one is what the ASSESSMENT
                  asked, this one is what the APPLICATION FORM asked, and the
                  two answer different questions about a candidate. Nothing here
                  is rated or interpreted. */}
              <TableHead className="w-[110px]">Validation</TableHead>
              {canDecide ? <TableHead className="w-[130px]">Team review</TableHead> : null}
              {canDecide ? <TableHead className="w-[130px]">Decision</TableHead> : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && rows.length === 0 ? (
              // Skeleton rows, not a spinner. The table's height stays put
              // while the page loads, so nothing below it jumps when the data
              // lands, and the shape tells the eye what is coming.
              Array.from({ length: 6 }).map((_, index) => (
                <TableRow key={`skeleton-${index}`} aria-hidden="true">
                  {Array.from({ length: columnCount }).map((__, cell) => (
                    <TableCell key={cell} className="py-4">
                      <Skeleton className="h-4 w-full rounded" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columnCount} className="py-10 text-center">
                  No candidates yet. Applications appear here as they arrive.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => (
                <TableRow key={row.link_id} className="align-top">
                  {selectable ? (
                    <TableCell className="pt-4">
                      <input
                        type="checkbox"
                        aria-label={`Select ${row.full_name}`}
                        className="h-4 w-4 accent-foreground"
                        checked={selected.has(row.link_id)}
                        onChange={() => toggle(row.link_id)}
                      />
                    </TableCell>
                  ) : null}
                  <TableCell className="pt-4 font-medium">
                    {row.full_name}
                    {/* COMPANY-JOB-CANDIDATE. Directly under the name, in a
                        monospace face so the groups line up down the column and
                        two codes can be compared by eye. Selectable, because
                        the point of it is being quoted into an email or a
                        ticket. It is a label, never a permission. */}
                    {row.reference_code ? (
                      <span
                        className="mt-0.5 block select-all font-mono text-[11px] font-normal tracking-wider"
                        title="Company, job and candidate reference"
                      >
                        {row.reference_code}
                      </span>
                    ) : null}
                    {row.archived_at ? (
                      <span className="mt-1 block text-xs">
                        Archived
                      </span>
                    ) : null}
                    {row.profile_age === "old" ? (
                      <span className="mt-1 block text-xs">
                        {row.profile_age_label}
                      </span>
                    ) : null}
                    {row.tier ? (
                      <span className="mt-1.5 block">
                        <TierBadge tier={row.tier} />
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell className="pt-4">
                    <ProcurementBadge
                      type={row.source_type}
                      label={row.source_type_label}
                    />
                  </TableCell>
                  <TableCell className="pt-4">
                    <StageBadge status={row.status} short />
                  </TableCell>
                  <TableCell className="pt-4">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!row.resume_url}
                      title={row.resume_url ? "View resume" : "No resume on file"}
                      onClick={() => onOpenResume(row)}
                    >
                      {row.resume_url ? "View" : "None"}
                    </Button>
                  </TableCell>
                  <TableCell className="pt-4">
                    <AiRatingCell row={row} onOpen={openCandidateDetail} />
                  </TableCell>
                  <TableCell className="pt-4">
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      disabled={!row.has_report}
                      title={
                        row.has_report
                          ? "Open the PPI Assessment Report"
                          : "This candidate has not completed their assessment yet"
                      }
                      onClick={() => onOpenReport(row)}
                    >
                      <FileText className="h-3.5 w-3.5" />
                      {row.has_report ? "Open" : "Pending"}
                    </Button>
                  </TableCell>
                  <TableCell className="pt-4">
                    {/* Deliberately NOT disabled on `has_report`. The
                        transcript is the evidence, not the conclusion: it
                        exists as soon as the candidate answers anything, and
                        the case a recruiter most wants it for is the assessment
                        that stalled halfway and therefore has no report. The
                        modal states "not opened yet" when there is nothing. */}
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      title="See the questions this candidate was asked and how they answered"
                      onClick={() => onOpenTranscript(row)}
                    >
                      <MessagesSquare className="h-3.5 w-3.5" />
                      View
                    </Button>
                  </TableCell>
                  <TableCell className="pt-4">
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      title="See the mandatory application questions and this candidate's exact answers"
                      onClick={() => setValidationRow(row)}
                    >
                      <ClipboardList className="h-3.5 w-3.5" />
                      View
                    </Button>
                  </TableCell>
                  {canDecide ? (
                    <TableCell className="pt-4">
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1"
                        onClick={() => setTeamReviewRow(row)}
                      >
                        <MessageSquareText className="h-3.5 w-3.5" />
                        Review
                      </Button>
                    </TableCell>
                  ) : null}
                  {canDecide ? (
                    <TableCell className="pt-4">
                      <StatusActions
                        linkId={row.link_id}
                        status={row.status}
                        allowed={row.allowed_transitions ?? []}
                        options={row.allowed_transition_options ?? []}
                        candidateName={row.full_name}
                        onChanged={load}
                      />
                    </TableCell>
                  ) : null}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* The full AI reasoning, opened from the AI Report button in a row. */}
      <AiRatingReportModal
        row={aiReportRow}
        open={aiReportRow !== null}
        onOpenChange={(next) => {
          if (!next) setAiReportRow(null);
        }}
      />
      <CandidateTeamReviewModal
        linkId={teamReviewRow?.link_id ?? null}
        candidateName={teamReviewRow?.full_name ?? "Candidate"}
        open={teamReviewRow !== null}
        onOpenChange={(next) => {
          if (!next) setTeamReviewRow(null);
        }}
      />
      <ValidationAnswersModal
        row={validationRow}
        open={validationRow !== null}
        onOpenChange={(next) => {
          if (!next) setValidationRow(null);
        }}
      />

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <BandLegend />
        {data && data.total_pages > 1 ? (
          <nav className="flex items-center gap-2" aria-label="Candidate pages">
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              disabled={!data.has_previous || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" /> Previous
            </Button>
            <span className="text-sm">
              Page {data.page} of {data.total_pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              disabled={!data.has_next || loading}
              onClick={() => setPage((p) => p + 1)}
            >
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          </nav>
        ) : null}
      </div>
    </section>
  );
}

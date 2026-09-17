"use client";

// Shared HR/Recruiter dashboard (FR-10.1/10.2): per-job funnel volumes, scoped
// by the backend to the caller's assignments.
//
// THE COUNTS HERE ARE PIPELINE VOLUMES, not rated output. The "no numbers reach
// a client" rule covers scores, ranks and percentages for an assessment or a
// match; it does not cover "four shortlisted". Nothing on this page is derived
// from a grade, and nothing here may ever be.
//
// WHAT THIS REPLACED, AND WHY. The previous version was six metric tiles above
// a table: a generic SaaS dashboard, and the exact shape DESIGN.md argues
// against. Three concrete faults, all fixed here:
//
//  1. EVERY NUMBER CARRIED EQUAL WEIGHT. Five of the six tiles were just the
//     table's column totals restated, so the page showed the same five facts
//     twice at two altitudes with no ranking between them. A recruiter opening
//     it could not tell in two seconds what needed them.
//  2. IT WAS A TERMINAL NODE. The job title was plain text, not a link, so the
//     one screen that tells you which job is stuck gave you no way to go and
//     unstick it.
//  3. EACH TILE CARRIED A ROUNDED ICON TILE, which DESIGN.md section 4 names
//     specifically as decoration that adds a shape without adding information.
//
// WHAT IT DELIBERATELY DOES NOT DO. There is no trend, no delta, no "since last
// week" and no date range, because `DashboardJobMetrics` carries five integers
// and no time dimension at all. Inventing a sparkline here would mean inventing
// the data under it. The honest move is to say more about the numbers that do
// exist, which is what the funnel and the attention list below do.
//
// There is also NO TEAL on this page. Teal means evidence in this system and
// nothing here is evidence; spending it on a volume chart would cost the one
// colour that carries meaning the thing that makes it mean something.

import * as React from "react";
import Link from "next/link";
import { ArrowUpDown, LayoutDashboard } from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import type { DashboardJobMetrics, DashboardSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/app-shell";
import { AnimatedList, AnimatedListItem } from "@/components/motion";
import {
  EmptyState,
  ErrorState,
  LoadingCards,
  RowCard,
} from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

/* -------------------------------------------------------------------------- */
/*  The funnel                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * The five volumes are a SEQUENCE, not five independent facts, and rendering
 * them as five identical tiles threw that away. Sourced people get shortlisted,
 * shortlisted people get offered, offered people join. Laid out in order with
 * each bar drawn against the widest stage, the shape of the pipeline is
 * readable without reading a single number, which is the whole job of the top
 * of this page.
 */
const FUNNEL_STAGES = [
  {
    key: "sourced" as const,
    label: "Sourced",
    hint: "Databank matches and freshly sourced candidates",
  },
  {
    key: "shortlisted" as const,
    label: "Shortlisted",
    hint: "Taken forward by your team",
  },
  { key: "offered" as const, label: "Offered", hint: "Offer extended" },
  { key: "joined" as const, label: "Joined", hint: "Started in the role" },
];

type FunnelTotals = {
  databank_matched: number;
  fresh_sourced: number;
  sourced: number;
  shortlisted: number;
  offered: number;
  joined: number;
};

function funnelTotals(jobs: DashboardJobMetrics[]): FunnelTotals {
  return jobs.reduce<FunnelTotals>(
    (acc, j) => ({
      databank_matched: acc.databank_matched + (j.databank_matched ?? 0),
      fresh_sourced: acc.fresh_sourced + (j.fresh_sourced ?? 0),
      sourced: acc.sourced + (j.databank_matched ?? 0) + (j.fresh_sourced ?? 0),
      shortlisted: acc.shortlisted + (j.shortlisted ?? 0),
      offered: acc.offered + (j.offered ?? 0),
      joined: acc.joined + (j.joined ?? 0),
    }),
    {
      databank_matched: 0,
      fresh_sourced: 0,
      sourced: 0,
      shortlisted: 0,
      offered: 0,
      joined: 0,
    },
  );
}

function Funnel({ totals }: { totals: FunnelTotals }) {
  // Every bar is drawn against the FIRST stage, not against its own neighbour.
  // Scaling each bar to the one before it would make a pipeline that loses nine
  // candidates in ten look identical to one that loses none, because every bar
  // would be full width.
  const widest = Math.max(totals.sourced, 1);

  return (
    <section
      aria-label="Pipeline funnel"
      className="border border-border bg-surface"
    >
      <div className="flex flex-col gap-1 border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold tracking-tight">
          Where your candidates are
        </h2>
        <p className="text-sm leading-6">
          Everyone currently in the pipeline across the jobs assigned to you.
        </p>
      </div>

      <ol className="divide-y divide-border">
        {FUNNEL_STAGES.map((stage) => {
          const value = totals[stage.key];
          const share = Math.round((value / widest) * 100);
          return (
            <li key={stage.key} className="px-5 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <p className="text-sm font-semibold">{stage.label}</p>
                <p className="text-2xl font-semibold [font-variant-numeric:tabular-nums]">
                  {value}
                </p>
              </div>
              <p className="mt-0.5 text-xs leading-5">{stage.hint}</p>
              {/* The bar is presentation only. Its value is stated as text
                  directly above, so the track carries aria-hidden rather than a
                  progressbar role that would read the same number twice. */}
              <div aria-hidden="true" className="mt-2 h-1.5 w-full bg-navy-50">
                <div
                  className="h-full bg-navy-600 transition-[width] duration-500 ease-out motion-reduce:transition-none"
                  style={{ width: share + "%" }}
                />
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*  Attention                                                                  */
/* -------------------------------------------------------------------------- */

type Attention = { job: DashboardJobMetrics; reason: string };

/**
 * Turns the same five integers into the only question the page can honestly
 * answer: which job should this person open next.
 *
 * BOTH SIGNALS ARE FACTS ABOUT THE ROW, never a prediction and never a score.
 * "Nobody shortlisted yet" is a statement about a count being zero, which is
 * exactly as true as the count itself. Anything cleverer than this would need
 * data the payload does not carry, and would be invented.
 */
function needsAttention(jobs: DashboardJobMetrics[]): Attention[] {
  const out: Attention[] = [];
  for (const job of jobs) {
    const inPipeline = (job.databank_matched ?? 0) + (job.fresh_sourced ?? 0);
    if (inPipeline > 0 && (job.shortlisted ?? 0) === 0) {
      out.push({
        job,
        reason: "Candidates are waiting and none are shortlisted yet",
      });
      continue;
    }
    const outstanding = (job.offered ?? 0) - (job.joined ?? 0);
    if (outstanding > 0) {
      out.push({
        job,
        reason:
          outstanding === 1
            ? "One offer is still open"
            : outstanding + " offers are still open",
      });
    }
  }
  return out;
}

function AttentionList({ items }: { items: Attention[] }) {
  if (items.length === 0) return null;
  return (
    <section
      aria-label="Jobs that need attention"
      className="border border-border bg-surface"
    >
      <div className="border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold tracking-tight">
          Worth a look first
        </h2>
        <p className="mt-1 text-sm leading-6">
          Drawn from the same counts below. Nothing here is a judgement about a
          candidate.
        </p>
      </div>
      <AnimatedList as="ul" className="divide-y divide-border">
        {items.map(({ job, reason }) => (
          <AnimatedListItem as="li" key={job.job_id}>
            <Link
              href={"/org/jobs/" + job.job_id}
              className="flex items-center justify-between gap-4 px-5 py-3.5 transition-colors duration-150 hover:bg-navy-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">
                  {job.title}
                </span>
                <span className="block text-xs leading-5">{reason}</span>
              </span>
              <span
                aria-hidden="true"
                className="shrink-0 text-sm font-semibold"
              >
                Open
              </span>
            </Link>
          </AnimatedListItem>
        ))}
      </AnimatedList>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*  The table                                                                  */
/* -------------------------------------------------------------------------- */

type SortKey = keyof Omit<DashboardJobMetrics, "job_id">;

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "title", label: "Job", numeric: false },
  { key: "databank_matched", label: "Databank", numeric: true },
  { key: "fresh_sourced", label: "Fresh", numeric: true },
  { key: "shortlisted", label: "Shortlisted", numeric: true },
  { key: "offered", label: "Offered", numeric: true },
  { key: "joined", label: "Joined", numeric: true },
];

export function DashboardSummaryView() {
  const [summary, setSummary] = React.useState<DashboardSummary | null>(null);
  const [loading, setLoading] = React.useState(true);
  // GET /dashboard/summary is gated on `view_dashboard`, which the Company
  // Admin (role `client`) does NOT hold by default. Collapsing that 403 into
  // the same "could not be loaded" state as a server fault is what made the
  // dashboard read as broken to the first person who signs a new customer up:
  // the answer is "not yours to see", and it does not improve on a reload.
  const [forbidden, setForbidden] = React.useState(false);

  // SORTING IS CLIENT SIDE HERE, AND THAT IS NOT THE RULE BEING BROKEN.
  // The house rule that a candidate table is sorted in SQL exists so the order
  // stays TOTAL across pages; sorting a page of rows in the browser makes rows
  // duplicate or vanish as you page. This response is the whole set in one
  // payload with no pagination, so there is no page boundary to desync and no
  // second request to disagree with. If this endpoint ever grows pagination,
  // this must move to the server with it.
  const [sort, setSort] = React.useState<{ key: SortKey; desc: boolean }>({
    key: "shortlisted",
    desc: true,
  });

  React.useEffect(() => {
    apiGet<DashboardSummary>("/dashboard/summary")
      .then(setSummary)
      .catch((error: unknown) => {
        setSummary(null);
        if (error instanceof ApiError && error.status === 403) setForbidden(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const jobs = React.useMemo(() => summary?.jobs ?? [], [summary]);
  const totals = React.useMemo(() => funnelTotals(jobs), [jobs]);
  const attention = React.useMemo(() => needsAttention(jobs), [jobs]);

  const sorted = React.useMemo(() => {
    const rows = [...jobs];
    rows.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      const cmp =
        typeof av === "string" && typeof bv === "string"
          ? av.localeCompare(bv)
          : Number(av) - Number(bv);
      // The title is compared as text, so its natural direction is A to Z while
      // a count's natural direction is largest first. Without this the "sort by
      // job" control would read as reversed.
      return sort.desc ? -cmp : cmp;
    });
    return rows;
  }, [jobs, sort]);

  function toggleSort(key: SortKey) {
    setSort((current) =>
      current.key === key
        ? { key, desc: !current.desc }
        : { key, desc: key !== "title" },
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Dashboard"
        description="Funnel volumes across the jobs assigned to you."
        actions={
          summary ? (
            <ExportXlsxButton
              fileName="readypick-customer-dashboard"
              rows={summary.jobs.map((job) => ({
                job: job.title,
                databank_matches: job.databank_matched,
                fresh_sourced: job.fresh_sourced,
                shortlisted: job.shortlisted,
                offered: job.offered,
                joined: job.joined,
              }))}
            />
          ) : null
        }
      />

      {loading ? (
        <LoadingCards
          count={4}
          className="lg:grid-cols-2"
          label="Loading dashboard"
        />
      ) : forbidden ? (
        <ErrorState
          title="The dashboard is not part of your access"
          description="Ask your Company Admin to grant you dashboard visibility."
        />
      ) : !summary ? (
        <ErrorState
          title="Dashboard unavailable"
          description="These figures could not be loaded. Reload the page to try again."
        />
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={LayoutDashboard}
          title="No jobs assigned yet"
          description="Once a job is assigned to you its funnel appears here."
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            <Funnel totals={totals} />
            <div className="space-y-6">
              <AttentionList items={attention} />
              <section className="border border-border bg-surface px-5 py-4">
                <h2 className="text-base font-semibold tracking-tight">
                  Jobs worked
                </h2>
                <p className="mt-1 text-sm leading-6">
                  Roles assigned to you across this period.
                </p>
                <p className="mt-2 text-3xl font-semibold [font-variant-numeric:tabular-nums]">
                  {summary.total_jobs_worked}
                </p>
              </section>
            </div>
          </div>

          <section
            aria-label="Per job volumes"
            className="border border-border bg-surface"
          >
            <div className="border-b border-border px-5 py-4">
              <h2 className="text-base font-semibold tracking-tight">
                Every job, stage by stage
              </h2>
            </div>

            <div className="hidden overflow-x-auto md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    {COLUMNS.map((col) => (
                      <TableHead
                        key={col.key}
                        className={col.numeric ? "text-right" : undefined}
                        aria-sort={
                          sort.key === col.key
                            ? sort.desc
                              ? "descending"
                              : "ascending"
                            : "none"
                        }
                      >
                        <button
                          type="button"
                          onClick={() => toggleSort(col.key)}
                          className={cn(
                            "inline-flex items-center gap-1.5 font-semibold transition-colors duration-150 hover:text-navy-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            col.numeric && "flex-row-reverse",
                          )}
                        >
                          {col.label}
                          <ArrowUpDown
                            className={cn(
                              "h-3.5 w-3.5 transition-opacity duration-150",
                              sort.key === col.key
                                ? "opacity-100"
                                : "opacity-40",
                            )}
                            aria-hidden="true"
                          />
                          <span className="sr-only">
                            {sort.key === col.key && !sort.desc
                              ? ", sorted ascending"
                              : sort.key === col.key
                                ? ", sorted descending"
                                : ", not sorted"}
                          </span>
                        </button>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sorted.map((j) => (
                    <TableRow
                      key={j.job_id}
                      className="transition-colors duration-150 hover:bg-navy-50"
                    >
                      <TableCell className="font-semibold">
                        {/* The job title was plain text before, which made this
                            the one screen that tells you a job is stalled and
                            gives you no way to go and fix it. */}
                        <Link
                          href={"/org/jobs/" + j.job_id}
                          className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {j.title}
                        </Link>
                      </TableCell>
                      <TableCell className="text-right">
                        {j.databank_matched}
                      </TableCell>
                      <TableCell className="text-right">
                        {j.fresh_sourced}
                      </TableCell>
                      <TableCell className="text-right">
                        {j.shortlisted}
                      </TableCell>
                      <TableCell className="text-right">{j.offered}</TableCell>
                      <TableCell className="text-right">{j.joined}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <ul className="divide-y divide-border md:hidden">
              {sorted.map((j) => (
                <li key={j.job_id} className="p-4">
                  <RowCard
                    title={
                      <Link
                        href={"/org/jobs/" + j.job_id}
                        className="underline-offset-4 hover:underline"
                      >
                        {j.title}
                      </Link>
                    }
                  >
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                      <Stat label="Databank" value={j.databank_matched} />
                      <Stat label="Fresh" value={j.fresh_sourced} />
                      <Stat label="Shortlisted" value={j.shortlisted} />
                      <Stat label="Offered" value={j.offered} />
                      <Stat label="Joined" value={j.joined} />
                    </dl>
                  </RowCard>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      {/* `opacity-80` here used to undo the "text is never grey" token at the
          call site. The label is separated from the value by weight instead. */}
      <dt>{label}</dt>
      <dd className="font-semibold [font-variant-numeric:tabular-nums]">
        {value}
      </dd>
    </div>
  );
}

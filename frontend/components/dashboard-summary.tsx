"use client";

// Shared HR/Recruiter dashboard (FR-10.1/10.2): per-job metrics + totals,
// scoped by the backend to the caller's assignments.
//
// The counts here are PIPELINE VOLUMES (how many people reached a stage), not
// rated output. The "no numbers reach a client" rule covers scores, ranks and
// percentages for an assessment or a match; it does not cover "4 shortlisted".

import * as React from "react";
import { LayoutDashboard, type LucideIcon } from "lucide-react";
import {
  BadgeCheck,
  Briefcase,
  Database,
  Handshake,
  UserPlus,
  Users,
} from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import type { DashboardSummary } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import {
  EmptyState,
  ErrorState,
  LoadingCards,
  RowCard,
} from "@/components/page-primitives";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

export function DashboardSummaryView() {
  const [summary, setSummary] = React.useState<DashboardSummary | null>(null);
  const [loading, setLoading] = React.useState(true);
  // GET /dashboard/summary is gated on `view_dashboard`, which the Company
  // Admin (role `client`) does NOT hold by default. Collapsing that 403 into
  // the same "could not be loaded" state as a server fault is what made the
  // dashboard read as broken to the first person who signs a new customer up:
  // the answer is "not yours to see", and it does not improve on a reload.
  const [forbidden, setForbidden] = React.useState(false);

  React.useEffect(() => {
    apiGet<DashboardSummary>("/dashboard/summary")
      .then(setSummary)
      .catch((error: unknown) => {
        setSummary(null);
        if (error instanceof ApiError && error.status === 403) setForbidden(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const totals = React.useMemo(() => {
    const jobs = summary?.jobs ?? [];
    return jobs.reduce(
      (acc, j) => ({
        databank_matched: acc.databank_matched + (j.databank_matched ?? 0),
        fresh_sourced: acc.fresh_sourced + (j.fresh_sourced ?? 0),
        shortlisted: acc.shortlisted + (j.shortlisted ?? 0),
        offered: acc.offered + (j.offered ?? 0),
        joined: acc.joined + (j.joined ?? 0),
      }),
      {
        databank_matched: 0,
        fresh_sourced: 0,
        shortlisted: 0,
        offered: 0,
        joined: 0,
      }
    );
  }, [summary]);

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
        <LoadingCards count={6} className="lg:grid-cols-3" label="Loading dashboard" />
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
      ) : (
        <div className="space-y-8">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <MetricCard
              icon={Briefcase}
              label="Jobs worked"
              value={summary.total_jobs_worked}
            />
            <MetricCard
              icon={Database}
              label="Databank matches"
              value={totals.databank_matched}
            />
            <MetricCard
              icon={UserPlus}
              label="Fresh sourced"
              value={totals.fresh_sourced}
            />
            <MetricCard
              icon={Users}
              label="Shortlisted"
              value={totals.shortlisted}
            />
            <MetricCard
              icon={Handshake}
              label="Offered"
              value={totals.offered}
            />
            <MetricCard
              icon={BadgeCheck}
              label="Joined"
              value={totals.joined}
            />
          </div>

          {summary.jobs.length === 0 ? (
            <EmptyState
              icon={LayoutDashboard}
              title="No jobs assigned yet"
              description="Once a job is assigned to you its funnel appears here."
            />
          ) : (
            <>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead className="text-right">Databank</TableHead>
                      <TableHead className="text-right">Fresh</TableHead>
                      <TableHead className="text-right">Shortlisted</TableHead>
                      <TableHead className="text-right">Offered</TableHead>
                      <TableHead className="text-right">Joined</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.jobs.map((j) => (
                      <TableRow key={j.job_id}>
                        <TableCell className="font-semibold">{j.title}</TableCell>
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

              <ul className="space-y-3 md:hidden">
                {summary.jobs.map((j) => (
                  <li key={j.job_id}>
                    <RowCard title={j.title}>
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
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="opacity-80">{label}</dt>
      <dd className="font-semibold [font-variant-numeric:tabular-nums]">
        {value}
      </dd>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: number;
}) {
  return (
    <Card className="shadow-card transition-shadow duration-150 hover:shadow-card-hover">
      <CardContent className="flex items-center gap-4 p-5">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-100 text-accent-foreground">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.08em] opacity-80">
            {label}
          </p>
          <p className="text-2xl font-bold [font-variant-numeric:tabular-nums]">
            {value}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

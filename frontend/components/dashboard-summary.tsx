"use client";

// Shared HR/Recruiter dashboard (FR-10.1/10.2): per-job metrics + totals,
// scoped by the backend to the caller's assignments.

import * as React from "react";

import { apiGet } from "@/lib/api";
import type { DashboardSummary } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function DashboardSummaryView() {
  const [summary, setSummary] = React.useState<DashboardSummary | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<DashboardSummary>("/dashboard/summary")
      .then(setSummary)
      .catch(() => setSummary(null))
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
        title="Dashboard"
        description="Per-job funnel metrics across your assigned jobs. Refreshed on a schedule."
      />

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : !summary ? (
        <p className="text-sm text-muted-foreground">
          Could not load dashboard data.
        </p>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
            <MetricCard
              label="Jobs worked"
              value={summary.total_jobs_worked}
            />
            <MetricCard
              label="Databank matches"
              value={totals.databank_matched}
            />
            <MetricCard label="Fresh sourced" value={totals.fresh_sourced} />
            <MetricCard label="Shortlisted" value={totals.shortlisted} />
            <MetricCard label="Offered" value={totals.offered} />
            <MetricCard label="Joined" value={totals.joined} />
          </div>

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
              {summary.jobs.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="text-center text-muted-foreground"
                  >
                    No jobs assigned yet.
                  </TableCell>
                </TableRow>
              ) : (
                summary.jobs.map((j) => (
                  <TableRow key={j.job_id}>
                    <TableCell className="font-medium">{j.title}</TableCell>
                    <TableCell className="text-right">
                      {j.databank_matched}
                    </TableCell>
                    <TableCell className="text-right">{j.fresh_sourced}</TableCell>
                    <TableCell className="text-right">{j.shortlisted}</TableCell>
                    <TableCell className="text-right">{j.offered}</TableCell>
                    <TableCell className="text-right">{j.joined}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-bold">{value}</p>
      </CardContent>
    </Card>
  );
}

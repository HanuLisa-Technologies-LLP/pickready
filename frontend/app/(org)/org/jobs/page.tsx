"use client";

// Shared org jobs list (PRD v1.0, flat staff roles). Visible to every staff
// member; all staff share one candidate pool. Creating a JD is gated by the
// data-driven capability `create_job` (granted to every staff role), and a
// created job is published directly, there is no approval chain to submit into.

import * as React from "react";
import Link from "next/link";
import { Archive, Briefcase, Plus, RotateCcw } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { Job } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  RowCard,
} from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { PostingWindowChip } from "@/components/posting-window";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default function OrgJobsPage() {
  const { toast } = useToast();
  const { hasCapability } = useAuth();
  const canCreate = hasCapability("create_job");

  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [failed, setFailed] = React.useState(false);
  const [workingId, setWorkingId] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const res = await apiGet<Job[] | { jobs: Job[] }>("/jobs?include_archived=true");
      setJobs(Array.isArray(res) ? res : res.jobs ?? []);
    } catch (e) {
      setFailed(true);
      toast({
        title: "Failed to load jobs",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const toggleArchive = async (job: Job) => {
    setWorkingId(job.id);
    try {
      const action = job.archived_at ? "restore" : "archive";
      const updated = await apiPost<Job>(`/jobs/${job.id}/${action}`);
      setJobs((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      toast({ title: job.archived_at ? "Job restored" : "Job archived" });
    } catch (error) {
      toast({
        title: "Could not update the job",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setWorkingId(null);
    }
  };

  const createButton = canCreate ? (
    <Button asChild>
      <Link href="/org/jobs/new">
        <Plus className="h-4 w-4" aria-hidden="true" /> Create JD
      </Link>
    </Button>
  ) : undefined;

  const archiveButton = (job: Job) => (
    <Button
      variant="ghost"
      size="sm"
      disabled={workingId === job.id}
      onClick={() => void toggleArchive(job)}
    >
      {job.archived_at ? (
        <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <Archive className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {job.archived_at ? "Restore" : "Archive"}
    </Button>
  );

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Jobs"
        description="Open a job to work on its details, matching and candidates."
        actions={createButton}
      />

      {loading ? (
        <LoadingRows rows={5} label="Loading jobs" />
      ) : failed ? (
        <ErrorState
          title="Could not load your jobs"
          description="The request did not complete. Try again in a moment."
          action={
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Briefcase}
          title="No jobs yet"
          description="Every job you create appears here with its posting window and candidates."
          action={createButton}
        />
      ) : (
        <>
          {/* Desktop: full table. Hidden under md so the page never scrolls sideways. */}
          <div className="hidden md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Department</TableHead>
                  <TableHead>Level</TableHead>
                  <TableHead>Requirement period</TableHead>
                  {/* Directive Part 3 §7.2: role type + credits per report on
                      every job row. Typography, not a coloured pill (Part 1
                      §24). */}
                  <TableHead>Role type</TableHead>
                  <TableHead>Posting</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="font-semibold">
                      <Link
                        href={`/org/jobs/${job.id}`}
                        className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {job.title}
                      </Link>
                    </TableCell>
                    <TableCell>{job.department}</TableCell>
                    <TableCell>{job.level}</TableCell>
                    <TableCell>{job.requirement_period}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {job.role_classification === "STEM" ? "STEM" : "Non-STEM"}
                      {" · "}
                      {(job.credit_cost_per_report ?? 1).toFixed(1)} credits/report
                    </TableCell>
                    <TableCell>
                      <PostingWindowChip job={job} />
                    </TableCell>
                    <TableCell>
                      {job.archived_at ? (
                        "Archived"
                      ) : (
                        <StatusBadge status={job.status} />
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {archiveButton(job)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Mobile: the same rows, stacked as cards. */}
          <ul className="space-y-3 md:hidden">
            {jobs.map((job) => (
              <li key={job.id}>
                <RowCard
                  title={
                    <Link
                      href={`/org/jobs/${job.id}`}
                      className="underline-offset-4 hover:underline"
                    >
                      {job.title}
                    </Link>
                  }
                  meta={
                    <>
                      {job.department}, {job.level}
                    </>
                  }
                  actions={archiveButton(job)}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {job.archived_at ? (
                      <span className="text-xs font-medium">Archived</span>
                    ) : (
                      <StatusBadge status={job.status} />
                    )}
                    <PostingWindowChip job={job} />
                  </div>
                  <p className="text-xs">
                    Requirement period: {job.requirement_period}
                  </p>
                  <p className="text-xs">
                    {job.role_classification === "STEM" ? "STEM" : "Non-STEM"} role
                    {" · "}
                    {(job.credit_cost_per_report ?? 1).toFixed(1)} credits per report
                  </p>
                </RowCard>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

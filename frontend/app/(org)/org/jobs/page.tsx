"use client";

// Shared org jobs list (PRD v1.0 — flat staff roles). Visible to every staff
// member; all staff share one candidate pool. Creating a JD is gated by the
// data-driven capability `create_job` (granted to every staff role), and a
// created job is published directly — there is no approval chain to submit into.

import * as React from "react";
import Link from "next/link";
import { Plus } from "lucide-react";

import { apiGet } from "@/lib/api";
import type { Job } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
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

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<Job[] | { jobs: Job[] }>("/jobs");
      setJobs(Array.isArray(res) ? res : res.jobs ?? []);
    } catch (e) {
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

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Jobs visible to you. Open one to work on its details, matching and candidates."
        actions={
          canCreate ? (
            <Button asChild className="gap-2">
              <Link href="/org/jobs/new">
                <Plus className="h-4 w-4" /> Create JD
              </Link>
            </Button>
          ) : undefined
        }
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Department</TableHead>
            <TableHead>Level</TableHead>
            <TableHead>Requirement period</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : jobs.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-muted-foreground">
                No jobs visible to you yet.
              </TableCell>
            </TableRow>
          ) : (
            jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell className="font-medium">
                  <Link
                    href={`/org/jobs/${job.id}`}
                    className="underline-offset-2 hover:underline"
                  >
                    {job.title}
                  </Link>
                </TableCell>
                <TableCell>{job.department}</TableCell>
                <TableCell>{job.level}</TableCell>
                <TableCell>{job.requirement_period}</TableCell>
                <TableCell>
                  <StatusBadge status={job.status} />
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

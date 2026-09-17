"use client";

// Shared ratified-jobs list for HR and Recruiter (they only see ratified, 
// the backend scopes GET /jobs by role).

import * as React from "react";
import Link from "next/link";

import { Briefcase } from "lucide-react";

import { apiGet } from "@/lib/api";
import type { Job } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState, LoadingRows } from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function JobsList({
  basePath,
  description,
}: {
  basePath: string;
  description: string;
}) {
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<Job[] | { jobs: Job[] }>("/jobs")
      .then((res) => setJobs(Array.isArray(res) ? res : res.jobs ?? []))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader title="Jobs" description={description} />
      {/* Loading and empty live OUTSIDE the table: neither a block of skeletons
          nor the shared empty state is valid markup as a child of TableBody,
          and a table header over nothing is a frame around an absence. */}
      {loading ? (
        <LoadingRows rows={5} label="Loading jobs" />
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Briefcase}
          title="No ratified jobs yet"
          description="A job appears here once it has been ratified."
        />
      ) : (
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
          {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell className="font-medium">
                  <Link
                    href={`${basePath}/${job.id}`}
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
          ))}
        </TableBody>
      </Table>
      )}
    </div>
  );
}

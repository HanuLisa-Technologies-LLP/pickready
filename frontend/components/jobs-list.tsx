"use client";

// Shared ratified-jobs list for HR and Recruiter (they only see ratified, 
// the backend scopes GET /jobs by role).

import * as React from "react";
import Link from "next/link";

import { apiGet } from "@/lib/api";
import type { Job } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";
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
              <TableCell colSpan={5} className="text-center">
                Loading
              </TableCell>
            </TableRow>
          ) : jobs.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center">
                No ratified jobs yet.
              </TableCell>
            </TableRow>
          ) : (
            jobs.map((job) => (
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
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

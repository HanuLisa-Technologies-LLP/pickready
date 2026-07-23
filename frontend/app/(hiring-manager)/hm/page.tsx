"use client";

// Hiring Manager jobs list + submit-for-approval action.

import * as React from "react";
import Link from "next/link";
import { Plus, Send } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { Job } from "@/lib/types";
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

export default function HmJobsPage() {
  const { toast } = useToast();
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

  const submit = async (job: Job) => {
    try {
      await apiPost(`/jobs/${job.id}/submit`);
      toast({
        title: "Submitted for approval",
        description: `${job.title} entered the approval chain.`,
      });
      void load();
    } catch (e) {
      toast({
        title: "Submit failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    }
  };

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Job descriptions you have created and their approval state."
        actions={
          <Button asChild className="gap-2">
            <Link href="/hm/jobs/new">
              <Plus className="h-4 w-4" /> Create JD
            </Link>
          </Button>
        }
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Department</TableHead>
            <TableHead>Level</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
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
                No jobs yet — create your first JD.
              </TableCell>
            </TableRow>
          ) : (
            jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell className="font-medium">{job.title}</TableCell>
                <TableCell>{job.department}</TableCell>
                <TableCell>{job.level}</TableCell>
                <TableCell>
                  <StatusBadge status={job.status} />
                </TableCell>
                <TableCell className="text-right">
                  {job.status === "draft" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-2"
                      onClick={() => void submit(job)}
                    >
                      <Send className="h-4 w-4" /> Submit
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

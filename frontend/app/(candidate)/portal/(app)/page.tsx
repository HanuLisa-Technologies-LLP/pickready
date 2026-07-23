"use client";

// New Jobs (FR-9.1): only jobs from tenants that have contacted this
// candidate. Applying requires a FRESH resume upload every time (FR-9.2) —
// nothing is prefilled or reused.

import * as React from "react";
import { Upload } from "lucide-react";

import { apiGet, apiUpload } from "@/lib/api";
import type { PortalJob } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export default function PortalJobsPage() {
  const { toast } = useToast();
  const [jobs, setJobs] = React.useState<PortalJob[]>([]);
  const [loading, setLoading] = React.useState(true);

  const [applyJob, setApplyJob] = React.useState<PortalJob | null>(null);
  const [resume, setResume] = React.useState<File | null>(null);
  const [applying, setApplying] = React.useState(false);

  React.useEffect(() => {
    apiGet<PortalJob[] | { jobs: PortalJob[] }>("/portal/jobs")
      .then((res) => setJobs(Array.isArray(res) ? res : res.jobs ?? []))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  const apply = async () => {
    if (!applyJob || !resume) return;
    setApplying(true);
    try {
      const fd = new FormData();
      fd.append("resume", resume);
      await apiUpload(`/portal/jobs/${applyJob.id}/apply`, fd);
      toast({
        title: "Application submitted",
        description: applyJob.title,
      });
      setApplyJob(null);
      setResume(null);
    } catch (e) {
      toast({
        title: "Application failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setApplying(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="New Jobs"
        description="Roles from employers that have previously reached out to you."
      />
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No jobs available yet. Jobs appear here after an employer first
          contacts you.
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {jobs.map((job) => (
            <Card key={job.id}>
              <CardHeader>
                <CardTitle className="text-base">{job.title}</CardTitle>
                <CardDescription>
                  {[job.company_name ?? job.tenant_name, job.department, job.level]
                    .filter(Boolean)
                    .join(" · ")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Button
                  size="sm"
                  className="gap-2"
                  onClick={() => {
                    setResume(null);
                    setApplyJob(job);
                  }}
                >
                  <Upload className="h-4 w-4" /> Apply
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={applyJob !== null}
        onOpenChange={(open) => {
          if (!open) {
            setApplyJob(null);
            setResume(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply — {applyJob?.title}</DialogTitle>
            <DialogDescription>
              A fresh resume upload is required for every application — we never
              store your resume between applications.
            </DialogDescription>
          </DialogHeader>
          <FormField label="Resume (fresh upload)" htmlFor="apply-resume" required>
            <Input
              id="apply-resume"
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={(e) => setResume(e.target.files?.[0] ?? null)}
            />
          </FormField>
          <DialogFooter>
            <Button
              disabled={applying || !resume}
              onClick={() => void apply()}
            >
              {applying ? "Submitting…" : "Submit application"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

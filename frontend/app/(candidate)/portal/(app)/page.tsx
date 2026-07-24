"use client";

// New Jobs (FR-9.1): jobs from tenants that have contacted this candidate.
// Applying accepts either a fresh resume upload or reuse of the last resume on
// record (FR-6.2). A public link (/apply/{job_uuid}) is the outreach-free path.

import * as React from "react";
import { Upload } from "lucide-react";

import { apiGet, apiUploadWithProgress } from "@/lib/api";
import type { PortalJob } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form";
import { ResumeFileInput } from "@/components/resume-file-input";
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
  const [resumeMode, setResumeMode] = React.useState<"upload" | "reuse">("upload");
  const [resume, setResume] = React.useState<File | null>(null);
  const [applying, setApplying] = React.useState(false);
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [resumeError, setResumeError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<PortalJob[] | { jobs: PortalJob[] }>("/portal/jobs")
      .then((res) => setJobs(Array.isArray(res) ? res : res.jobs ?? []))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  const apply = async () => {
    if (!applyJob) return;
    if (resumeMode === "upload" && !resume) return;
    setApplying(true);
    setUploadProgress(0);
    setResumeError(null);
    try {
      const fd = new FormData();
      fd.append("aspects", "{}");
      if (resumeMode === "reuse") {
        fd.append("reuse_previous", "true");
      } else if (resume) {
        fd.append("resume", resume);
      }
      await apiUploadWithProgress(`/portal/jobs/${applyJob.id}/apply`, fd, setUploadProgress);
      toast({
        title: "Application submitted",
        description: applyJob.title,
      });
      setApplyJob(null);
      setResume(null);
      setResumeMode("upload");
    } catch (e) {
      setResumeError(e instanceof Error ? e.message : "Application failed. Please retry.");
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
                    setResumeMode("upload");
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
            setResumeMode("upload");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply — {applyJob?.title}</DialogTitle>
            <DialogDescription>
              Upload a fresh resume, or reuse the last one on your record.
            </DialogDescription>
          </DialogHeader>
          <div
            className="grid gap-2 sm:grid-cols-2"
            role="radiogroup"
            aria-label="Resume option"
          >
            <Button
              type="button"
              variant={resumeMode === "upload" ? "secondary" : "outline"}
              aria-pressed={resumeMode === "upload"}
              onClick={() => setResumeMode("upload")}
            >
              Upload a new resume
            </Button>
            <Button
              type="button"
              variant={resumeMode === "reuse" ? "secondary" : "outline"}
              aria-pressed={resumeMode === "reuse"}
              onClick={() => {
                setResumeMode("reuse");
                setResume(null);
              }}
            >
              Reuse my last resume
            </Button>
          </div>
          {resumeMode === "upload" ? (
            <FormField label="Resume file" htmlFor="apply-resume" required>
              <ResumeFileInput
                id="apply-resume"
                file={resume}
                progress={uploadProgress}
                error={resumeError}
                disabled={applying}
                onFileChange={(file, error) => {
                  setResume(file);
                  setResumeError(error);
                  setUploadProgress(0);
                }}
                onRetry={() => void apply()}
              />
            </FormField>
          ) : (
            <p className="text-sm text-muted-foreground">
              We&apos;ll attach the most recent resume on your record.
            </p>
          )}
          <DialogFooter>
            <Button
              disabled={applying || (resumeMode === "upload" && !resume)}
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

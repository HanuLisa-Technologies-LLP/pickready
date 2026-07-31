"use client";

// New Jobs (FR-9.1).
//
// What the board shows (client decision, 2026-07-27):
//   * RELEVANT roles only, ranked by the backend against this candidate's main
//     resume, its parsed skills, and their profile form, not the whole
//     cross-tenant catalogue;
//   * a search box, which deliberately bypasses relevance so a candidate can
//     always find a role they know the name of.
//
// Applying is now one continuous flow: read the JD and the company, choose the
// main resume or upload a new one, submit, and go straight into the
// assessment. The application and its resume are saved BEFORE the questions
// start, so closing the tab mid-assessment loses nothing; Applied Jobs offers
// "Continue" for anything unfinished.

import * as React from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Briefcase, MapPin, Search, Upload, X } from "lucide-react";

import { ApiError, apiGet, apiUploadWithProgress } from "@/lib/api";
import type { PortalJob } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import {
  ApplicationValidationForm,
  missingValidationFields,
  type ValidationFieldSpec,
  type ValidationValues,
} from "@/components/application-validation-form";
import {
  CompanySummary,
  JobDescriptionSummary,
  hasCompanyContent,
  pickJd,
} from "@/components/job-description";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  ResumeChoice,
  type ResumeMode,
  type StoredResume,
} from "@/components/resume-file-input";
import { EmptyState, LoadingCards } from "@/components/page-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Stagger, StaggerItem } from "@/components/motion";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface ApplyContext {
  job_id: string;
  already_applied: boolean;
  applied_at?: string | null;
  resume: StoredResume;
  profile_complete: boolean;
  profile_missing: string[];
  /** The six mandatory fields (spec §7), defined server-side. */
  validation_fields?: ValidationFieldSpec[];
}

interface ApplyResult {
  link_id: string;
  job_id: string;
}

const SEARCH_DEBOUNCE_MS = 300;

export default function PortalJobsPage() {
  const { toast } = useToast();
  const router = useRouter();
  const [jobs, setJobs] = React.useState<PortalJob[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [activeSearch, setActiveSearch] = React.useState("");

  const [applyJob, setApplyJob] = React.useState<PortalJob | null>(null);
  const [resumeMode, setResumeMode] = React.useState<ResumeMode>("upload");
  const [resume, setResume] = React.useState<File | null>(null);
  const [context, setContext] = React.useState<ApplyContext | null>(null);
  const [contextLoading, setContextLoading] = React.useState(true);
  const [applying, setApplying] = React.useState(false);
  const [validation, setValidation] = React.useState<ValidationValues>({});
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [resumeError, setResumeError] = React.useState<string | null>(null);
  // The list endpoint may omit the JD, so the dialog fetches the full job on
  // open, a candidate must see what they are applying to before uploading.
  const [applyJobFull, setApplyJobFull] = React.useState<PortalJob | null>(null);
  const [jdLoading, setJdLoading] = React.useState(false);

  // Debounced so typing doesn't fire a request per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setActiveSearch(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const query = activeSearch
      ? `?search=${encodeURIComponent(activeSearch)}`
      : "";
    apiGet<PortalJob[] | { jobs: PortalJob[] }>(`/portal/jobs${query}`)
      .then((res) => {
        if (cancelled) return;
        setJobs(Array.isArray(res) ? res : res.jobs ?? []);
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSearch]);

  const resetDialog = () => {
    setApplyJob(null);
    setApplyJobFull(null);
    setResume(null);
    setResumeError(null);
    setUploadProgress(0);
    setValidation({});
  };

  const openApply = (job: PortalJob) => {
    setResume(null);
    setResumeError(null);
    setApplyJob(job);
    setApplyJobFull(job);
    setContextLoading(true);
    setJdLoading(true);

    // Apply context first: it decides whether "use my main resume" is even
    // offerable and whether the profile form is complete.
    apiGet<ApplyContext>(`/portal/jobs/${job.id}/apply-context`)
      .then((res) => {
        setContext(res);
        setResumeMode(res.resume.has_resume ? "reuse" : "upload");
      })
      .catch(() => setContext(null))
      .finally(() => setContextLoading(false));

    // Then the full job, so About/Culture and the JD are complete even when the
    // list response was trimmed.
    apiGet<PortalJob>(`/portal/jobs/${job.id}`)
      .then((full) => setApplyJobFull({ ...job, ...full }))
      .catch(() => {
        /* Keep the list row; a partial JD must never block applying. */
      })
      .finally(() => setJdLoading(false));
  };

  const apply = async () => {
    if (!applyJob) return;
    if (resumeMode === "upload" && !resume) {
      setResumeError("Attach a PDF or DOCX resume (up to 10 MB).");
      return;
    }
    if (resumeMode === "reuse" && !context?.resume.has_resume) {
      setResumeError(
        "There is no main resume on your profile yet. Upload one instead."
      );
      return;
    }
    // Checked before the upload starts: a 422 after a 10 MB resume has already
    // been sent is a bad way to learn you missed a dropdown.
    const missing = missingValidationFields(context?.validation_fields ?? [], validation);
    if (missing.length > 0) {
      setResumeError(`Please complete every required field: ${missing.join(", ")}`);
      return;
    }
    setApplying(true);
    setUploadProgress(0);
    setResumeError(null);
    try {
      const fd = new FormData();
      if (resumeMode === "reuse") {
        fd.append("reuse_previous", "true");
      } else if (resume) {
        fd.append("resume", resume);
      }
      fd.append("validation", JSON.stringify(validation));
      const result = await apiUploadWithProgress<ApplyResult>(
        `/portal/jobs/${applyJob.id}/apply`,
        fd,
        setUploadProgress
      );
      toast({
        title: "Application submitted",
        description: `${applyJob.title}. Next, answer the assessment questions.`,
      });
      resetDialog();
      // Straight into the assessment: JD -> resume -> questions is one flow.
      router.push(`/portal/assessments/${result.link_id}`);
    } catch (e) {
      const message =
        e instanceof ApiError && e.status === 409
          ? "You've already applied to this job."
          : e instanceof Error
            ? e.message
            : "Application failed. Please retry.";
      setResumeError(message);
      toast({
        title: "Application failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setApplying(false);
    }
  };

  const dialogJob = applyJobFull ?? applyJob;
  const subtitle = [
    dialogJob?.company_name ?? dialogJob?.tenant_name,
    dialogJob?.department,
    dialogJob?.level,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div>
      <PageHeader
        title="New Jobs"
        description="Roles matched to your profile and your main resume."
      />

      <div className="mb-8 max-w-md">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-70"
            aria-hidden="true"
          />
          <Input
            type="search"
            className="pl-10 pr-10"
            placeholder="Search by job role"
            value={search}
            aria-label="Search jobs by role"
            onChange={(event) => setSearch(event.target.value)}
          />
          {search ? (
            <button
              type="button"
              className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md hover:bg-brand-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Clear search"
              onClick={() => setSearch("")}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      {loading ? (
        <LoadingCards count={6} label="Loading jobs" />
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={Briefcase}
          title={
            activeSearch ? "No roles match that search" : "No matching roles yet"
          }
          description={
            activeSearch
              ? "Try a shorter phrase, or clear the search to see roles matched to your profile."
              : "Add your main resume and finish your profile so we can match you to open roles."
          }
          action={
            activeSearch ? (
              <Button variant="outline" onClick={() => setSearch("")}>
                Clear search
              </Button>
            ) : (
              <Button asChild>
                <Link href="/portal/profile">Go to My Profile</Link>
              </Button>
            )
          }
        />
      ) : (
        <Stagger className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {jobs.map((job) => (
            <StaggerItem key={job.id}>
              <Card className="flex h-full flex-col shadow-card transition-shadow duration-150 hover:shadow-card-hover">
                <CardContent className="flex flex-1 flex-col gap-4 p-6">
                  <div className="min-w-0 space-y-2">
                    <h2 className="text-balance text-base font-semibold">
                      {job.title}
                    </h2>
                    <p className="flex items-center gap-1.5 text-sm leading-6">
                      <MapPin
                        className="h-3.5 w-3.5 shrink-0 opacity-70"
                        aria-hidden="true"
                      />
                      <span className="truncate">
                        {[
                          job.company_name ?? job.tenant_name,
                          job.department,
                          job.level,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </p>
                  </div>
                  <Button className="mt-auto w-full" onClick={() => openApply(job)}>
                    <Upload className="h-4 w-4" aria-hidden="true" /> Apply
                  </Button>
                </CardContent>
              </Card>
            </StaggerItem>
          ))}
        </Stagger>
      )}

      <Dialog
        open={applyJob !== null}
        onOpenChange={(open) => {
          if (!open) resetDialog();
        }}
      >
        <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Apply: {dialogJob?.title}</DialogTitle>
            {subtitle ? <p className="text-sm leading-6">{subtitle}</p> : null}
          </DialogHeader>

          {/* The role AND the employer are stated in full before anything is
              asked of the candidate. */}
          <JobDescriptionSummary jd={pickJd(dialogJob)} loading={jdLoading} />
          {hasCompanyContent(dialogJob) ? (
            <>
              <Separator />
              <CompanySummary job={dialogJob} />
            </>
          ) : null}

          <Separator />

          {context && !context.profile_complete ? (
            <div className="rounded-xl border border-border bg-brand-100/50 p-4 text-sm leading-6">
              <p className="font-semibold">Your profile is not complete yet.</p>
              <p className="mt-1">
                Employers see your profile answers alongside this application.
                You can still apply now, then{" "}
                <Link
                  className="font-semibold text-brand-600 underline underline-offset-4"
                  href="/portal/profile"
                >
                  complete My Profile
                </Link>
                .
              </p>
            </div>
          ) : null}

          <ResumeChoice
            id="apply-resume"
            mode={resumeMode}
            onModeChange={setResumeMode}
            stored={context?.resume ?? null}
            storedLoading={contextLoading}
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

          <ApplicationValidationForm
            fields={context?.validation_fields ?? []}
            values={validation}
            onChange={setValidation}
            disabled={applying}
          />

          <DialogFooter>
            <Button disabled={applying} onClick={() => void apply()}>
              {applying
                ? uploadProgress > 0 && uploadProgress < 100
                  ? `Uploading ${uploadProgress}%`
                  : "Submitting"
                : "Submit and start assessment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

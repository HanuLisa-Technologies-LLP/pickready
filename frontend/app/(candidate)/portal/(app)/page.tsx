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
// Applying is one form, shared with the public /apply page
// (`components/apply-form.tsx`): read the JD and the company, choose the main
// resume or upload a new one, answer the six validation fields, submit. It
// does NOT lead into an assessment: the hiring team invites candidates to the
// assessment one by one, and the application is followed on Applied Jobs.
//
// A role the candidate already applied to says so ON THE CARD and links to the
// application, so nobody opens a form that could only answer "you have
// already applied".

import * as React from "react";
import Link from "next/link";
import { Briefcase, CheckCircle2, MapPin, Search, Upload, X } from "lucide-react";

import { apiGet } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type { PortalJob } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { ApplyForm, applicationHref } from "@/components/apply-form";
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
  EmptyState,
  ErrorState,
  InlineError,
  LoadingCards,
} from "@/components/page-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Stagger, StaggerItem } from "@/components/motion";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const SEARCH_DEBOUNCE_MS = 300;

export default function PortalJobsPage() {
  const [jobs, setJobs] = React.useState<PortalJob[]>([]);
  const [loading, setLoading] = React.useState(true);
  // A failed read is its own state. Rendering it as an empty board told a
  // candidate there were no roles for them when the request had simply failed.
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);
  const [search, setSearch] = React.useState("");
  const [activeSearch, setActiveSearch] = React.useState("");

  const [applyJob, setApplyJob] = React.useState<PortalJob | null>(null);
  // The list endpoint may omit the JD, so the dialog fetches the full job on
  // open: a candidate must see what they are applying to before uploading.
  const [applyJobFull, setApplyJobFull] = React.useState<PortalJob | null>(null);
  const [jdLoading, setJdLoading] = React.useState(false);
  const [jdError, setJdError] = React.useState<string | null>(null);
  // The job the dialog is open for, so a slow JD read for a dialog already
  // closed (or reopened on another role) cannot land in the wrong one.
  const openJobId = React.useRef<string | null>(null);

  // Debounced so typing doesn't fire a request per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setActiveSearch(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const query = activeSearch
      ? `?search=${encodeURIComponent(activeSearch)}`
      : "";
    apiGet<PortalJob[] | { jobs: PortalJob[] }>(`/portal/jobs${query}`)
      .then((res) => {
        if (cancelled) return;
        setJobs(Array.isArray(res) ? res : res.jobs ?? []);
      })
      .catch((failure) => {
        if (cancelled) return;
        setJobs([]);
        setLoadError(apiErrorMessage(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSearch, attempt]);

  const closeDialog = () => {
    openJobId.current = null;
    setApplyJob(null);
    setApplyJobFull(null);
    setJdError(null);
  };

  const openApply = (job: PortalJob) => {
    openJobId.current = job.id;
    setApplyJob(job);
    setApplyJobFull(job);
    setJdLoading(true);
    setJdError(null);
    apiGet<PortalJob>(`/portal/jobs/${job.id}`)
      .then((full) => {
        if (openJobId.current === job.id) setApplyJobFull({ ...job, ...full });
      })
      .catch((failure) => {
        if (openJobId.current !== job.id) return;
        // The list row is kept and the form below loads on its own, so a
        // trimmed JD never blocks applying; the candidate is told the full
        // description did not arrive rather than shown the summary as if it
        // were all there is.
        setJdError(apiErrorMessage(failure));
      })
      .finally(() => {
        if (openJobId.current === job.id) setJdLoading(false);
      });
  };

  // The card flips to "applied" the moment the server confirms, so the board
  // never offers a second application for the same role.
  const markApplied = (jobId: string, applicationId: string) =>
    setJobs((current) =>
      current.map((job) =>
        job.id === jobId
          ? { ...job, already_applied: true, application_id: applicationId }
          : job
      )
    );

  const dialogJob = applyJobFull ?? applyJob;
  const subtitle = [
    dialogJob?.company_name ?? dialogJob?.tenant_name,
    dialogJob?.department,
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
      ) : loadError ? (
        <ErrorState
          title="We could not load the jobs board"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          }
        />
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
                    <p className="flex items-center gap-1.5 text-sm">
                      <MapPin
                        className="h-3.5 w-3.5 shrink-0 opacity-70"
                        aria-hidden="true"
                      />
                      <span className="truncate">
                        {(() => {
                          const employer =
                            job.company_name ?? job.tenant_name ?? null;
                          const rest = job.department ?? "";
                          return (
                            <>
                              {employer && job.company_slug ? (
                                // The company name links to its public
                                // employer page only when that page is
                                // actually served (company_slug is null
                                // for a hidden page).
                                <Link
                                  href={`/employers/${job.company_slug}`}
                                  className="font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                >
                                  {employer}
                                </Link>
                              ) : (
                                employer
                              )}
                              {employer && rest ? " · " : null}
                              {rest || null}
                            </>
                          );
                        })()}
                      </span>
                    </p>
                  </div>
                  {job.already_applied ? (
                    <div className="mt-auto space-y-2">
                      <p className="flex items-center gap-1.5 text-sm font-medium">
                        <CheckCircle2
                          className="h-4 w-4 text-brand-600"
                          aria-hidden="true"
                        />
                        You have applied
                      </p>
                      <Button asChild variant="outline" className="w-full">
                        <Link href={applicationHref(job.application_id)}>
                          View application
                        </Link>
                      </Button>
                    </div>
                  ) : (
                    <Button className="mt-auto w-full" onClick={() => openApply(job)}>
                      <Upload className="h-4 w-4" aria-hidden="true" /> Apply
                    </Button>
                  )}
                </CardContent>
              </Card>
            </StaggerItem>
          ))}
        </Stagger>
      )}

      <Dialog
        open={applyJob !== null}
        onOpenChange={(open) => {
          if (!open) closeDialog();
        }}
      >
        <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Apply: {dialogJob?.title}</DialogTitle>
            {subtitle ? <p className="text-sm">{subtitle}</p> : null}
          </DialogHeader>

          {/* The role AND the employer are stated in full before anything is
              asked of the candidate. */}
          <JobDescriptionSummary jd={pickJd(dialogJob)} loading={jdLoading} />
          {jdError ? (
            <InlineError>
              The full job description could not be loaded. {jdError}
            </InlineError>
          ) : null}
          {hasCompanyContent(dialogJob) ? (
            <>
              <Separator />
              <CompanySummary job={dialogJob} />
            </>
          ) : null}

          <Separator />

          {applyJob ? (
            <ApplyForm
              key={applyJob.id}
              jobId={applyJob.id}
              jobTitle={applyJob.title}
              companyName={applyJob.company_name ?? applyJob.tenant_name}
              source="direct"
              applicationId={applyJob.application_id}
              onSubmitted={(result) => markApplied(applyJob.id, result.link_id)}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

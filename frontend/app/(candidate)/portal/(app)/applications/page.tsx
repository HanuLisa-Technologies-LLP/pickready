"use client";

// Applied Jobs (spec §8.1 / §2.3).
//
// Grouped by where the POSTING is in its 30-day lifecycle, because that is what
// decides what the candidate can still do:
//
//   Open        the posting is live, the application is in progress
//   Closing     applications have closed, but this one can still be edited
//   Closed      the window has fully passed; read-only
//
// Each card carries the pipeline status, the full status timeline, and, only
// when the backend says so, the assessment link and the edit action. The
// assessment link is gated on `assessment_invited`, NEVER on the application
// existing: not every applicant is assessed, and offering a link that 403s
// would be worse than offering none.
//
// Clicking the job title still opens the full JD (client decision,
// 2026-07-27): a candidate reviewing what they applied to should not have to
// go hunting for it.

import * as React from "react";
import { useRouter } from "next/navigation";
import { CalendarClock, FileText, ListChecks, PencilLine } from "lucide-react";

import { apiGet } from "@/lib/api";
import {
  PIPELINE_LABELS,
  type PipelineStage,
  type PortalJob,
  type PostingStatus,
  type StatusEvent,
} from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { StageBadge } from "@/components/pipeline-status";
import { ApplicationEditModal } from "@/components/application-edit-modal";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
} from "@/components/page-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  CompanySummary,
  JobDescriptionSummary,
  hasCompanyContent,
  pickJd,
} from "@/components/job-description";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

/** Mirrors `schemas.portal.ApplicationOut`. */
interface ApplicationRow {
  link_id: string;
  job_id: string;
  job_title: string;
  company_name?: string | null;
  applied_at?: string | null;
  status: PipelineStage;
  stage_label: string;
  status_updated_at?: string | null;
  timeline: StatusEvent[];
  posting_status?: PostingStatus | null;
  posting_end_date?: string | null;
  grace_period_end_date?: string | null;
  can_edit: boolean;
  edit_closes_at?: string | null;
  days_until_edit_closes: number;
  assessment_invited: boolean;
  assessment_completed: boolean;
  conversation_status?: string | null;
  report_ready?: boolean;
}

const GROUPS: {
  key: string;
  title: string;
  blurb: string;
  match: (a: ApplicationRow) => boolean;
}[] = [
  {
    key: "open",
    title: "Open",
    blurb: "These roles are still accepting applications.",
    match: (a) => a.posting_status === "active",
  },
  {
    key: "closing",
    title: "Closing soon",
    blurb:
      "Applications have closed for these roles, but you can still update yours for a few more days.",
    match: (a) => a.posting_status === "grace_period",
  },
  {
    key: "closed",
    title: "Closed",
    blurb: "These postings have ended. Your application is read-only.",
    match: (a) =>
      a.posting_status !== "active" && a.posting_status !== "grace_period",
  },
];

function formatDate(value?: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** The candidate-visible status history, oldest first so it reads as a path. */
function Timeline({ events }: { events: StatusEvent[] }) {
  if (events.length === 0) return null;
  return (
    <ol className="mt-1 space-y-2 border-l border-border pl-4">
      {events.map((event, index) => (
        <li key={`${event.status}-${event.at}`} className="relative text-xs">
          <span
            aria-hidden="true"
            className={`absolute -left-[21px] top-1 h-2 w-2 rounded-full ring-2 ring-surface ${
              index === events.length - 1 ? "bg-brand-600" : "bg-border"
            }`}
          />
          <span className="font-semibold">
            {PIPELINE_LABELS[event.status as PipelineStage] ?? event.label}
          </span>
          <span className="ml-2 opacity-80">
            {new Date(event.at).toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
            })}
          </span>
        </li>
      ))}
    </ol>
  );
}

function ApplicationCard({
  application,
  onEdit,
  onOpenJob,
}: {
  application: ApplicationRow;
  onEdit: (a: ApplicationRow) => void;
  onOpenJob: (a: ApplicationRow) => void;
}) {
  const router = useRouter();
  const assessmentOpen =
    application.assessment_invited && !application.assessment_completed;

  return (
    <Card className="h-full shadow-card transition-shadow duration-150 hover:shadow-card-hover">
      <CardContent className="space-y-4 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <button
              type="button"
              className="text-balance text-left text-base font-semibold underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => onOpenJob(application)}
            >
              {application.job_title || "Untitled role"}
            </button>
            <p className="mt-1 text-sm leading-6">
              {application.company_name ?? "-"}, applied{" "}
              {formatDate(application.applied_at)}
            </p>
          </div>
          <StageBadge status={application.status} />
        </div>

        {application.posting_end_date ? (
          <p className="flex flex-wrap items-center gap-1.5 text-xs leading-5">
            <CalendarClock className="h-3.5 w-3.5 opacity-70" aria-hidden="true" />
            Applications closed {formatDate(application.posting_end_date)}
            {application.can_edit && application.days_until_edit_closes > 0 ? (
              <span className="font-semibold">
                , edit window closes in {application.days_until_edit_closes}{" "}
                {application.days_until_edit_closes === 1 ? "day" : "days"}
              </span>
            ) : null}
          </p>
        ) : null}

        <Timeline events={application.timeline} />

        <div className="flex flex-wrap gap-2 pt-1">
          {assessmentOpen ? (
            <Button
              size="sm"
              onClick={() =>
                router.push(`/portal/assessments/${application.link_id}`)
              }
            >
              <FileText className="h-3.5 w-3.5" aria-hidden="true" />
              {application.conversation_status === "active"
                ? "Continue assessment"
                : "Start assessment"}
            </Button>
          ) : null}
          {application.can_edit ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onEdit(application)}
            >
              <PencilLine className="h-3.5 w-3.5" aria-hidden="true" />
              Update application
            </Button>
          ) : null}
        </div>

        {!application.assessment_invited ? (
          <p className="text-xs leading-5">
            The hiring team invites candidates to the assessment individually.
            You will be emailed if they invite you.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function PortalApplicationsPage() {
  const [applications, setApplications] = React.useState<ApplicationRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<ApplicationRow | null>(null);
  const [jobDialog, setJobDialog] = React.useState<{
    title: string;
    job: PortalJob | null;
  } | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<
        ApplicationRow[] | { applications: ApplicationRow[] }
      >("/portal/applications");
      setApplications(Array.isArray(res) ? res : res.applications ?? []);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not load your applications."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const openJob = async (application: ApplicationRow) => {
    setJobDialog({ title: application.job_title, job: null });
    try {
      const job = await apiGet<PortalJob>(`/portal/jobs/${application.job_id}`);
      setJobDialog({ title: application.job_title, job });
    } catch {
      // A closed posting 404s by design (spec Rule 3/4). The dialog stays open
      // with an explanation rather than silently doing nothing.
      setJobDialog({ title: application.job_title, job: null });
    }
  };

  return (
    <div>
      <PageHeader
        title="Applied Jobs"
        description="Every role you have applied to, and where each one stands."
        actions={
          applications.length ? (
            <ExportXlsxButton
              fileName="readypick-my-applications"
              rows={applications.map((application) => ({
                role: application.job_title,
                company: application.company_name ?? "",
                applied_on: formatDate(application.applied_at),
                status: application.stage_label,
                posting_status: application.posting_status ?? "closed",
                assessment_invited: application.assessment_invited,
                assessment_completed: application.assessment_completed,
              }))}
            />
          ) : null
        }
      />

      {loading ? (
        <LoadingRows rows={3} label="Loading your applications" />
      ) : error ? (
        <ErrorState
          title="Could not load your applications"
          description={error}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      ) : applications.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="No applications yet"
          description="Roles you apply to appear here with their status and next step."
          action={
            <Button asChild>
              <a href="/portal">Browse New Jobs</a>
            </Button>
          }
        />
      ) : (
        <div className="space-y-8">
          {GROUPS.map((group) => {
            const rows = applications.filter(group.match);
            if (rows.length === 0) return null;
            return (
              <section key={group.key} aria-label={group.title}>
                <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-600">
                  {group.title}
                </h2>
                <p className="mb-4 mt-1.5 text-sm leading-6">{group.blurb}</p>
                <div className="grid gap-4 lg:grid-cols-2">
                  {rows.map((application) => (
                    <ApplicationCard
                      key={application.link_id}
                      application={application}
                      onEdit={setEditing}
                      onOpenJob={openJob}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <ApplicationEditModal
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        linkId={editing?.link_id ?? null}
        jobTitle={editing?.job_title ?? ""}
        daysRemaining={editing?.days_until_edit_closes ?? 0}
        onSaved={load}
      />

      <Dialog
        open={jobDialog !== null}
        onOpenChange={(open) => !open && setJobDialog(null)}
      >
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{jobDialog?.title}</DialogTitle>
          </DialogHeader>
          {jobDialog?.job ? (
            <div className="space-y-4">
              <JobDescriptionSummary jd={pickJd(jobDialog.job)} />
              {hasCompanyContent(jobDialog.job) ? (
                <>
                  <Separator />
                  <CompanySummary job={jobDialog.job} />
                </>
              ) : null}
            </div>
          ) : (
            <p className="py-8 text-center text-sm leading-6">
              This posting has closed, so its full description is no longer
              available.
            </p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

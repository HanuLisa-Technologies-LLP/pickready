"use client";

// The job detail page (2026-07-27 spec §2/§3/§7). This IS the review screen,
// there is no separate one any more.
//
// Two tabs. The JOB DESCRIPTION tab is the job's setup, in the order the work
// happens (Vivekium release, Phase 1, owner ruling D1):
//
//   [ JD: the one markdown document + the job's details (grade, band, ...) ]
//   [ SWOT analysis (Bodha)                                                ]
//   [ Skills: Must-have / Nice-to-have / Behavioural, at most five each    ]
//   [ Assessment monitoring                                                ]
//   [ Publish: the checklist and the one publish action                    ]
//
// The CANDIDATES tab carries the databank upload, AI matching, invitations
// and the ranked table.
//
// What is gone, and why:
//   * The per-section JD editor. The markdown document is canonical and is
//     edited as one document through PATCH /jobs/{id}/jd; a second editor that
//     re-rendered the document from sections discarded the recruiter's own
//     formatting. The job's details (title, grade, band, narrative sections)
//     are a separate, smaller form through PATCH /jobs/{id}.
//   * `level`. The grade and the experience band replaced it; the badge reads
//     "Grade", never "Level".
//   * The Tatva matrix editor and the Matching Categories card
//     (`JobSetupReview`). The Skills panel replaces both.
//
// Deliberately absent, per the spec: the "Added by HR after ratification"
// metadata, the notes textbox, the approval-status display, and the separate
// JD-edits card.

import * as React from "react";
import { Loader2, Pencil, Send, Sparkles } from "lucide-react";
import { useParams } from "next/navigation";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import {
  JOB_GRADES,
  jobGradeLabel,
  jobJd,
  type CompanyProfile,
  type Job,
  type JobGrade,
  type MatchingTaskStatus,
  type RankedCandidate,
} from "@/lib/types";
import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { RoleTypeBadge } from "@/components/role-type-badge";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { JdDocument } from "@/components/jd-document";
import { CandidateRankingTable } from "@/components/candidate-ranking-table";
import { DatabankUpload } from "@/components/databank-upload";
import { PipelineFunnel } from "@/components/pipeline-status";
import { PostingWindowBanner } from "@/components/posting-window";
import { EmailCompositionModal } from "@/components/email-composition-modal";
import { JobSwotAnalysisPanel } from "@/components/job-swot-analysis";
import { JobSkillsPanel } from "@/components/job-skills";
import {
  JobPublishCard,
  type JobSetupStatus,
} from "@/components/job-publish-card";
import { MonitoringPolicyCard } from "@/components/proctoring/monitoring-policy-card";
import { PPIReportModal } from "@/components/ppi-report-modal";
import {
  MatchingReasoning,
  type MatchingProgress,
} from "@/components/matching-reasoning";
import {
  AiActivityIndicator,
  useAiActivity,
} from "@/components/ai-activity";
import type {
  AiTransportState,
  CarriesAiActivity,
} from "@/lib/ai-activity";
import { AssessmentTranscriptModal } from "@/components/assessment-transcript";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Why the grade field is disabled. The grade locks with the skills the moment
 * a candidate starts the assessment (D5), because it decides the question
 * budget every candidate on the job receives. Same words the server refuses a
 * grade change with.
 */
const GRADE_LOCKED_SENTENCE =
  "The grade is locked because a candidate has started the assessment. It decides the question budget every candidate on this job receives.";

/** What the Close Job toast says. Closing withholds the assessment records
 *  from the team at once (2026-09-22); the pipeline is NOT unchanged. */
const JOB_CLOSED_SENTENCE =
  "New applications have stopped. This job's assessment records are now withheld from the hiring team and are deleted when the retention window ends.";

/** A narrative section, with a note when it is inherited from the company. */
function NarrativeSection({
  label,
  value,
  inherited,
}: {
  label: string;
  value?: string | null;
  inherited: boolean;
}) {
  if (!value || !value.trim()) return null;
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <h4 className="font-semibold">{label}</h4>
        {inherited ? (
          <Badge variant="secondary" className="text-[10px]">
            From company profile
          </Badge>
        ) : null}
      </div>
      <p className="whitespace-pre-line">{value}</p>
    </div>
  );
}

/** The job's details: everything a recruiter edits that is not the document. */
type DetailsDraft = {
  title: string;
  department: string;
  grade: JobGrade | "";
  requirement_period: string;
  experience_min_years: string;
  experience_max_years: string;
  about_company: string;
  work_life: string;
  benefits: string;
};

function detailsFromJob(job: Job): DetailsDraft {
  return {
    title: job.title ?? "",
    department: job.department ?? "",
    grade: job.grade ?? "",
    requirement_period: job.requirement_period ?? "",
    experience_min_years:
      job.experience_min_years === null || job.experience_min_years === undefined
        ? ""
        : String(job.experience_min_years),
    experience_max_years:
      job.experience_max_years === null || job.experience_max_years === undefined
        ? ""
        : String(job.experience_max_years),
    // Pre-filled with the RESOLVED value (spec §3.1): opening the editor shows
    // the company's text rather than an empty box, so a recruiter who only
    // wanted to tweak a sentence does not have to retype the paragraph.
    about_company: job.about_company ?? "",
    work_life: job.work_life ?? "",
    benefits: job.benefits ?? "",
  };
}

function yearsOrNull(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function experienceBand(job: Job): string | null {
  const low = job.experience_min_years;
  const high = job.experience_max_years;
  if (low === null || low === undefined || high === null || high === undefined) {
    return null;
  }
  return `${low} to ${high} years`;
}

export default function OrgJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const { toast } = useToast();
  const { can: hasCapability } = usePermissions();

  const canEditJd = hasCapability(CAP.editJobDescription);
  const canRunMatching = hasCapability(CAP.triggerMatching);
  const canEmail = hasCapability(CAP.sendOutreach);
  const canDecide = hasCapability(CAP.decideProfile);
  const canUploadDatabank = hasCapability(CAP.uploadResumes);
  const canRenew = hasCapability(CAP.publishJob);
  // The capability half of "may re-draft the skills": all three buckets. The
  // Skills panel re-checks with the server's per-job answer.
  const canRedraftSkills =
    hasCapability(CAP.editMustHaveSkills) &&
    hasCapability(CAP.editNiceToHaveSkills) &&
    hasCapability(CAP.editBehaviouralCompetencies);

  // Which of the two top-level screens is showing. The JD opens first: a
  // recruiter arriving at a job usually wants to check the posting before the
  // applicants.
  const [tab, setTab] = React.useState<"jd" | "candidates">("jd");

  const [job, setJob] = React.useState<Job | null>(null);
  const [company, setCompany] = React.useState<CompanyProfile | null>(null);
  // Distinguishes "the job has not arrived yet" from "the job could not be
  // read". Without it a failed load left `job` null forever and the card below
  // showed its loading skeleton for the rest of the session: the job
  // description simply never appeared, and the toast that said why was long
  // gone by the time anyone looked.
  const [jobError, setJobError] = React.useState<string | null>(null);

  // The one JD document, edited as one document.
  const [editingDoc, setEditingDoc] = React.useState(false);
  const [docDraft, setDocDraft] = React.useState("");
  const [savingDoc, setSavingDoc] = React.useState(false);
  // The job's details, a separate and smaller form.
  const [editingDetails, setEditingDetails] = React.useState(false);
  const [details, setDetails] = React.useState<DetailsDraft | null>(null);
  const [savingDetails, setSavingDetails] = React.useState(false);

  // The setup answer the Publish card reads, shared so the grade field locks
  // from the same server answer the checklist shows.
  const [setup, setSetup] = React.useState<JobSetupStatus | null>(null);
  // Bumped when a SWOT save may have started a skills draft.
  const [skillsReloadKey, setSkillsReloadKey] = React.useState(0);
  // Bumped whenever anything the publish checklist reads may have changed.
  const [checklistReloadKey, setChecklistReloadKey] = React.useState(0);
  // Bumped by the SWOT panel's "Re-draft skills from the updated SWOT".
  const [redraftSignal, setRedraftSignal] = React.useState(0);
  const refreshChecklist = React.useCallback(
    () => setChecklistReloadKey((key) => key + 1),
    []
  );

  const [matchingState, setMatchingState] = React.useState<
    "idle" | "running" | "done" | "error"
  >("idle");
  const [matchingMessage, setMatchingMessage] = React.useState(
    "Ready to score candidates."
  );
  /** The live stage list, straight from the task's own Celery state. Null
   *  until the first poll answers, so the panel is absent rather than empty. */
  const [matchingProgress, setMatchingProgress] =
    React.useState<MatchingProgress | null>(null);
  /**
   * The dispatched run id, which is also this AI operation's activity id.
   *
   * One identifier for both, rather than a second one invented for the status:
   * the browser is holding the run id before the task has been picked up, so
   * every activity payload is attributable from the first poll, and a second
   * run started from another tab cannot repaint this one (Case 3 section 24).
   */
  const [matchingRunId, setMatchingRunId] = React.useState("");
  const matchingActivity = useAiActivity(matchingRunId);
  /**
   * The latest activity view, for the poll loop below.
   *
   * `runMatching` is an async function, so it closes over the view from the
   * render it was called in, and that view still carries the PREVIOUS run id.
   * Reporting through it would file the new run's payloads under the old
   * operation and every one of them would be discarded as foreign.
   */
  const matchingActivityRef = React.useRef(matchingActivity);
  matchingActivityRef.current = matchingActivity;
  const [reloadKey, setReloadKey] = React.useState(0);

  const [reportRow, setReportRow] = React.useState<RankedCandidate | null>(null);
  const [transcriptRow, setTranscriptRow] = React.useState<RankedCandidate | null>(null);
  const [emailRows, setEmailRows] = React.useState<RankedCandidate[]>([]);
  const [selectedRows, setSelectedRows] = React.useState<RankedCandidate[]>([]);
  const [inviting, setInviting] = React.useState(false);
  const [renewing, setRenewing] = React.useState(false);
  const [closing, setClosing] = React.useState(false);
  const [closeOpen, setCloseOpen] = React.useState(false);
  const [closeReason, setCloseReason] = React.useState("");

  /** Take a fresh job read as the page's truth, and reset both editors to it. */
  const acceptJob = React.useCallback((next: Job) => {
    setJob(next);
    setDocDraft(next.jd_markdown ?? "");
    setDetails(detailsFromJob(next));
  }, []);

  /**
   * Re-open an expired posting for another fixed 30-day window.
   *
   * Everyone who applied to the previous run keeps their application and stays
   * in the candidate table; they simply read as Old Profiles from here on. The
   * table is reloaded alongside the job so that relabelling is visible at once
   * rather than on the next navigation.
   */
  const renewPosting = React.useCallback(async () => {
    setRenewing(true);
    try {
      const updated = await apiPost<Job>(`/jobs/${jobId}/renew`);
      acceptJob(updated);
      setReloadKey((key) => key + 1);
      toast({
        title: "Posting renewed",
        description: "This job is live again for another 30 days.",
      });
    } catch (e) {
      toast({
        title: "Could not renew this posting",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRenewing(false);
    }
  }, [jobId, toast, acceptJob]);

  /**
   * Close the posting because the requirement is met (workflow Gate 8).
   *
   * Confirmed in a dialog rather than fired from the banner button, because
   * there is no reopen: RBAC 22 asks for a controlled revision mechanism, and
   * a misclick here takes a live posting off every candidate's board. The
   * dialog is also where the reason is typed -- the client's own words, stored
   * verbatim, read by nobody but their team.
   */
  const closePosting = React.useCallback(async () => {
    setClosing(true);
    try {
      const updated = await apiPost<Job>(`/jobs/${jobId}/close`, {
        reason: closeReason.trim() || null,
      });
      acceptJob(updated);
      setCloseOpen(false);
      setCloseReason("");
      refreshChecklist();
      toast({ title: "Job closed", description: JOB_CLOSED_SENTENCE });
    } catch (e) {
      toast({
        title: "Could not close this job",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setClosing(false);
    }
  }, [jobId, closeReason, toast, acceptJob, refreshChecklist]);

  const loadJob = React.useCallback(async () => {
    setJobError(null);
    try {
      const res = await apiGet<Job>(`/jobs/${jobId}`);
      acceptJob(res);
    } catch (e) {
      setJobError(
        e instanceof Error ? e.message : "This job could not be loaded."
      );
      toast({
        title: "Failed to load job",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    }
  }, [jobId, toast, acceptJob]);

  React.useEffect(() => {
    void loadJob();
    // The company name is needed for the email placeholders. A failure here is
    // not worth a toast, the modal falls back to a neutral phrase.
    apiGet<CompanyProfile>("/companies/me/profile")
      .then(setCompany)
      .catch(() => setCompany(null));
  }, [loadJob]);

  /** Save the one JD document. The server re-derives every section from it. */
  const saveDocument = async () => {
    if (!job) return;
    setSavingDoc(true);
    try {
      const updated = await apiPatch<Job>(`/jobs/${jobId}/jd`, {
        jd_markdown: docDraft,
      });
      acceptJob(updated);
      setEditingDoc(false);
      refreshChecklist();
      toast({ title: "Job description updated" });
    } catch (e) {
      toast({
        title: "Couldn't save the job description",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSavingDoc(false);
    }
  };

  /** Save the job's details. The grade is sent only when it changed, so a
   *  locked job can still have its title or narrative sections edited. */
  const saveDetails = async () => {
    if (!details || !job) return;
    setSavingDetails(true);
    try {
      const updated = await apiPatch<Job>(`/jobs/${jobId}`, {
        title: details.title.trim(),
        department: details.department.trim() || null,
        requirement_period: details.requirement_period.trim() || null,
        experience_min_years: yearsOrNull(details.experience_min_years),
        experience_max_years: yearsOrNull(details.experience_max_years),
        ...(details.grade && details.grade !== job.grade
          ? { grade: details.grade }
          : {}),
        // Sending null (not "") clears the per-job override so the section
        // falls back to the company profile, the two mean different things.
        about_company: details.about_company.trim() || null,
        work_life: details.work_life.trim() || null,
        benefits: details.benefits.trim() || null,
      });
      acceptJob(updated);
      setEditingDetails(false);
      refreshChecklist();
      toast({ title: "Job details updated" });
    } catch (e) {
      toast({
        title: "Couldn't save the job details",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSavingDetails(false);
    }
  };

  // Only applicants still at `applied` can be invited; the backend refuses the
  // rest and reports them, but filtering here keeps the button honest about
  // how many it will actually send.
  const invitable = selectedRows.filter((r) => r.status === "applied");

  // Gate 5: a sourced candidate has not applied, so there is nothing to
  // assess. What they get is an invitation to APPLY, which is a different
  // action to a different endpoint. Keeping the two buttons separate is
  // deliberate: one merged control would have to guess which the recruiter
  // meant from the selection, and guessing wrong emails the wrong copy to a
  // real person.
  const sourcedSelected = selectedRows.filter((r) => r.status === "sourced");
  const [invitingToApply, setInvitingToApply] = React.useState(false);

  const inviteToApply = async () => {
    setInvitingToApply(true);
    try {
      const res = await apiPost<{
        invited: number;
        skipped: number;
        results: { invited: boolean; reason?: string | null }[];
      }>(`/jobs/${jobId}/candidates/databank/invite`, {
        link_ids: sourcedSelected.map((r) => r.link_id),
      });
      const firstReason = res.results.find((r) => !r.invited)?.reason;
      toast({
        title: `${res.invited} invitation${res.invited === 1 ? "" : "s"} sent`,
        description:
          res.skipped > 0
            ? `${res.skipped} skipped. ${firstReason ?? ""}`.trim()
            : "They stay out of your pipeline until they apply themselves.",
      });
      setReloadKey((k) => k + 1);
    } catch (e) {
      toast({
        title: "Couldn't send the invitations",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setInvitingToApply(false);
    }
  };

  const sendInvitations = async () => {
    setInviting(true);
    try {
      const res = await apiPost<{ invited: number; skipped: unknown[] }>(
        `/pipeline/jobs/${jobId}/select-candidates`,
        { link_ids: selectedRows.map((r) => r.link_id) }
      );
      toast({
        title: `${res.invited} assessment invitation${res.invited === 1 ? "" : "s"} sent`,
        description:
          res.skipped.length > 0
            ? `${res.skipped.length} skipped, already past this stage.`
            : "Only invited candidates can take the assessment.",
      });
      setReloadKey((k) => k + 1);
    } catch (e) {
      toast({
        title: "Couldn't send the invitations",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setInviting(false);
    }
  };

  const runMatching = async () => {
    setMatchingState("running");
    setMatchingProgress(null);
    // Clears the previous run's activity before this one has an id of its own,
    // so the last line of a finished run is never sitting under a button that
    // has just been pressed again.
    setMatchingRunId("");
    setMatchingMessage("Starting the run.");
    try {
      const res = await apiPost<{ candidate_count: number; task_id: string }>(
        `/jobs/${jobId}/run-matching`
      );
      setMatchingRunId(res.task_id);
      let finished = false;
      let finalState = "PENDING";
      for (let attempt = 0; attempt < 240; attempt += 1) {
        const status = await apiGet<MatchingTaskStatus & CarriesAiActivity>(
          `/matching/tasks/${res.task_id}`
        );
        finalState = status.state;
        // The AI activity line, from the same response the stage list comes
        // from, so the two cannot be read a poll apart from each other. The
        // field is optional: a response without it leaves the indicator silent
        // rather than the page inventing a status it was not given.
        matchingActivityRef.current.report(
          status.activity,
          status.state as AiTransportState
        );
        // The stage list is always returned, including for a task still sitting
        // in the queue, so the panel draws the whole plan at once and fills it
        // in rather than appearing to invent steps as it goes.
        if (status.stages?.length) {
          setMatchingProgress({
            stages: status.stages,
            candidate_count: status.candidate_count ?? 0,
            scored_count: status.scored_count ?? 0,
          });
        }
        setMatchingMessage(
          status.state === "PENDING"
            ? "Waiting for a worker to pick the run up."
            : ""
        );
        if (status.done) {
          finished = true;
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
      if (!finished) {
        throw new Error("AI matching is taking longer than six minutes. The job is still running; refresh this page to check its results.");
      }
      if (finalState !== "SUCCESS") {
        throw new Error(`AI matching ended in ${finalState.toLowerCase()} state. No partial result is being presented as complete.`);
      }
      setMatchingState("done");
      setMatchingMessage(
        `${res.candidate_count} candidate${res.candidate_count === 1 ? "" : "s"} scored. Matching is complete.`
      );
      setReloadKey((key) => key + 1);
      toast({
        title: "AI matching complete",
        description: `${res.candidate_count} candidate${res.candidate_count === 1 ? "" : "s"} scored and ready to review.`,
      });
    } catch (e) {
      setMatchingState("error");
      // Ends the activity immediately. Without this the last line the run
      // reported stays on screen describing a step that is no longer running,
      // which is the failure Case 3 section 25 names.
      matchingActivityRef.current.fail();
      setMatchingMessage(
        e instanceof Error ? e.message : "AI matching could not be started."
      );
      toast({
        title: "Could not start matching",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    }
  };

  const overridden = new Set(job?.overridden_sections ?? []);
  const jd = job ? jobJd(job) : {};
  const gradeLocked = Boolean(setup?.grade_locked);

  return (
    <div>
      {/* The AI matching run used to open a modal here that could not be
          dismissed and showed one unchanging sentence for its whole duration.
          It is now the <MatchingReasoning> panel below the button: the run
          reports each stage as the pipeline reaches it, and the recruiter keeps
          the page while it works. */}
      <PageHeader
        eyebrow="Customer Portal"
        title={job?.title ?? "Job"}
        description={
          job
            ? [job.department, experienceBand(job), job.requirement_period]
                .filter(Boolean)
                .join(" · ")
            : undefined
        }
        actions={
          job ? (
            <div className="flex flex-col items-end gap-2">
              {/* Directive Part 3 Rule 5: the classification is VISIBLE here
                  and editable nowhere. Disputes go to support. */}
              <RoleTypeBadge
                classification={job.role_classification}
                creditCost={job.credit_cost_per_report}
              />
              <Badge variant="secondary">Grade: {jobGradeLabel(job.grade)}</Badge>
            </div>
          ) : undefined
        }
      />

      {job ? (
        <PostingWindowBanner
          job={job}
          className="mb-6"
          onRenew={canRenew ? () => void renewPosting() : undefined}
          renewing={renewing}
          onClose={canRenew ? () => setCloseOpen(true) : undefined}
          closing={closing}
        />
      ) : null}

      {/* ── MOUNT POINT: AssessmentRetentionPanel (Phase 6) ─────────────────
          Phase 6 builds components/assessment-retention-panel.tsx (the
          thirty day retention state and the assessment dispute path for a
          CLOSED job). It is mounted HERE, under the posting banner, and only
          for a closed job:

            {job?.closed_at ? <AssessmentRetentionPanel jobId={jobId} /> : null}

          Deliberately not imported by Phase 1: the component does not exist
          on this branch. */}

      <Dialog open={closeOpen} onOpenChange={setCloseOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Close this job?</DialogTitle>
            <DialogDescription>
              New applications stop immediately and the public link stops
              working. Every candidate already in your pipeline stays, but the
              assessment data for this job, the PRISM Reports, the assessment
              scores, the interview transcripts and any recordings, becomes
              unavailable to your team the moment you close it: candidates
              consented to their assessment data on the basis that it lives
              only as long as this position. It is kept for 30 more days and
              can be retrieved in that time only through the assessment
              dispute process. After 30 days it is permanently deleted and
              cannot be recovered. Your ranked list, pipeline stages and
              billing records remain. There is no reopen, so those 30 days
              are the only way back from closing the wrong job.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label htmlFor="close_reason" className="text-sm font-medium">
              Why are you closing it? Optional.
            </label>
            <Textarea
              id="close_reason"
              value={closeReason}
              onChange={(e) => setCloseReason(e.target.value)}
              maxLength={1000}
              placeholder="Two offers accepted."
            />
            <p className="text-xs">
              Your team sees this on the job page. No candidate ever does.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCloseOpen(false)}>
              Keep it open
            </Button>
            <Button onClick={() => void closePosting()} disabled={closing}>
              {closing ? "Closing" : "Close the job"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Two buttons at the top of the page (client change, 2026-07-28): the
          job description and the candidate list are separate screens now
          rather than one very long scroll. */}
      <div
        role="tablist"
        aria-label="Job sections"
        className="mb-6 inline-flex rounded-xl border border-border bg-secondary p-1"
      >
        {(["jd", "candidates"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              "rounded-lg px-4 py-2 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              tab === key
                ? "bg-brand-600 font-semibold text-white shadow-brand"
                : "font-medium hover:bg-brand-100/70 hover:text-accent-foreground"
            )}
          >
            {key === "jd" ? "Job description" : "Candidates"}
          </button>
        ))}
      </div>

      {/* ── Job description: the one document ───────────────────────────── */}
      <Card className={cn("mb-6", tab !== "jd" && "hidden")}>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Job description</CardTitle>
            <CardDescription>
              Reporting to {String(jd.reporting_to || "-")}
            </CardDescription>
          </div>
          {job && canEditJd && !editingDoc ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDocDraft(job.jd_markdown ?? "");
                setEditingDoc(true);
              }}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit description
            </Button>
          ) : null}
        </CardHeader>

        <CardContent className="space-y-4 text-sm">
          {!job && jobError ? (
            <ErrorState
              title="This job could not be loaded"
              description={jobError}
              action={
                <Button variant="outline" onClick={() => void loadJob()}>
                  Try again
                </Button>
              }
            />
          ) : !job ? (
            <LoadingRows rows={4} label="Loading the job description" />
          ) : editingDoc ? (
            <div className="space-y-3">
              <Textarea
                aria-label="Job description document"
                className="min-h-[420px] font-mono text-[13px] leading-6"
                value={docDraft}
                onChange={(e) => setDocDraft(e.target.value)}
              />
              <div className="flex gap-2">
                <Button
                  disabled={savingDoc || !docDraft.trim()}
                  onClick={() => void saveDocument()}
                >
                  {savingDoc ? "Saving" : "Save description"}
                </Button>
                <Button
                  variant="outline"
                  disabled={savingDoc}
                  onClick={() => {
                    setDocDraft(job.jd_markdown ?? "");
                    setEditingDoc(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (job.jd_markdown ?? "").trim() ? (
            <JdDocument markdown={job.jd_markdown ?? ""} />
          ) : (
            <p>No job description has been written for this job yet.</p>
          )}
        </CardContent>
      </Card>

      {/* ── The job's details: everything that is not the document ───────── */}
      <Card className={cn("mb-6", tab !== "jd" && "hidden")}>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Job details</CardTitle>
            <CardDescription>
              The grade decides which assessment candidates receive.
            </CardDescription>
          </div>
          {job && canEditJd && !editingDetails ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDetails(detailsFromJob(job));
                setEditingDetails(true);
              }}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit details
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {!job ? (
            jobError ? null : <LoadingRows rows={3} label="Loading the job details" />
          ) : editingDetails && details ? (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Title" htmlFor="job-title" required>
                  <Input
                    id="job-title"
                    value={details.title}
                    onChange={(e) => setDetails({ ...details, title: e.target.value })}
                  />
                </FormField>
                <FormField label="Department" htmlFor="job-dept">
                  <Input
                    id="job-dept"
                    value={details.department}
                    onChange={(e) =>
                      setDetails({ ...details, department: e.target.value })
                    }
                  />
                </FormField>
                <FormField
                  label="Grade"
                  htmlFor="job-grade"
                  hint={
                    gradeLocked
                      ? GRADE_LOCKED_SENTENCE
                      : "Decides which assessment applicants receive."
                  }
                >
                  <Select
                    value={details.grade}
                    disabled={gradeLocked}
                    onValueChange={(v) =>
                      setDetails({ ...details, grade: v as JobGrade })
                    }
                  >
                    <SelectTrigger id="job-grade">
                      <SelectValue placeholder="Select a grade" />
                    </SelectTrigger>
                    <SelectContent>
                      {JOB_GRADES.map((g) => (
                        <SelectItem key={g.value} value={g.value}>
                          {g.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormField>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Experience from (years)" htmlFor="job-exp-min">
                  <Input
                    id="job-exp-min"
                    type="number"
                    min={0}
                    max={60}
                    value={details.experience_min_years}
                    onChange={(e) =>
                      setDetails({ ...details, experience_min_years: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Experience to (years)" htmlFor="job-exp-max">
                  <Input
                    id="job-exp-max"
                    type="number"
                    min={0}
                    max={60}
                    value={details.experience_max_years}
                    onChange={(e) =>
                      setDetails({ ...details, experience_max_years: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Requirement period" htmlFor="job-period">
                  <Input
                    id="job-period"
                    value={details.requirement_period}
                    onChange={(e) =>
                      setDetails({ ...details, requirement_period: e.target.value })
                    }
                  />
                </FormField>
              </div>

              <FormField
                label="About company"
                htmlFor="job-about"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="job-about"
                  rows={4}
                  value={details.about_company}
                  onChange={(e) =>
                    setDetails({ ...details, about_company: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="Work life"
                htmlFor="job-worklife"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="job-worklife"
                  rows={4}
                  value={details.work_life}
                  onChange={(e) => setDetails({ ...details, work_life: e.target.value })}
                />
              </FormField>
              <FormField
                label="Benefits"
                htmlFor="job-benefits"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="job-benefits"
                  rows={4}
                  value={details.benefits}
                  onChange={(e) => setDetails({ ...details, benefits: e.target.value })}
                />
              </FormField>

              <div className="flex gap-2">
                <Button
                  disabled={savingDetails || !details.title.trim()}
                  onClick={() => void saveDetails()}
                >
                  {savingDetails ? "Saving" : "Save details"}
                </Button>
                <Button
                  variant="outline"
                  disabled={savingDetails}
                  onClick={() => {
                    setDetails(detailsFromJob(job));
                    setEditingDetails(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <dl className="grid gap-3 sm:grid-cols-3">
                <div>
                  <dt className="font-semibold">Grade</dt>
                  <dd>{jobGradeLabel(job.grade)}</dd>
                </div>
                <div>
                  <dt className="font-semibold">Experience</dt>
                  <dd>{experienceBand(job) ?? "-"}</dd>
                </div>
                <div>
                  <dt className="font-semibold">Requirement period</dt>
                  <dd>{job.requirement_period || "-"}</dd>
                </div>
              </dl>
              {gradeLocked ? <p className="text-xs">{GRADE_LOCKED_SENTENCE}</p> : null}
              <NarrativeSection
                label="About company"
                value={job.about_company}
                inherited={!overridden.has("about_company")}
              />
              <NarrativeSection
                label="Work life"
                value={job.work_life}
                inherited={!overridden.has("work_life")}
              />
              <NarrativeSection
                label="Benefits"
                value={job.benefits}
                inherited={!overridden.has("benefits")}
              />
            </>
          )}
        </CardContent>
      </Card>

      {/* The SWOT analysis sits under the JD because that is what it is about:
          this role's hiring position, drafted from this JD. It renders its own
          permission-aware states, so there is no capability check here. Its
          first save starts the skills draft on the server; a later one can
          only OFFER a re-draft, which opens the Skills panel's confirmation. */}
      {job ? (
        <JobSwotAnalysisPanel
          jobId={job.id}
          className={cn(tab !== "jd" && "hidden")}
          onSaved={() => {
            setSkillsReloadKey((key) => key + 1);
            refreshChecklist();
          }}
          canRedraftSkills={canRedraftSkills}
          onRequestSkillsRedraft={() => setRedraftSignal((n) => n + 1)}
        />
      ) : null}

      {/* The Skills step (D1): what every candidate is assessed against. It
          replaced the Tatva matrix editor and the Matching Categories card. */}
      {job ? (
        <JobSkillsPanel
          jobId={job.id}
          reloadKey={skillsReloadKey}
          redraftSignal={redraftSignal}
          onChanged={refreshChecklist}
          className={cn(tab !== "jd" && "hidden")}
        />
      ) : null}

      {/* The one monitoring setting, moved here from the deleted setup review:
          it is part of setting the job up, not of reviewing candidates. */}
      {job ? (
        <div className={cn("mb-6", tab !== "jd" && "hidden")}>
          <MonitoringPolicyCard jobId={job.id} />
        </div>
      ) : null}

      {/* The one place a job goes live. */}
      {job ? (
        <JobPublishCard
          jobId={job.id}
          job={job}
          reloadKey={checklistReloadKey}
          onSetup={setSetup}
          onPublished={() => {
            void loadJob();
          }}
          className={cn(tab !== "jd" && "hidden")}
        />
      ) : null}

      {/* Everything below is the Candidates screen. Hidden rather than
          unmounted so switching tabs does not refetch the table or lose a
          recruiter's tick-box selection. */}
      <div className={cn(tab !== "candidates" && "hidden")}>

      {/* ── Databank upload (up to 25 resumes at once) ──────────────────── */}
      {canUploadDatabank ? (
        <DatabankUpload
          jobId={jobId}
          onUploaded={() => setReloadKey((k) => k + 1)}
          className="mb-6"
        />
      ) : null}

      {/* ── Run AI matching ─────────────────────────────────────────────── */}

      <div className="my-6 space-y-3 rounded-xl border border-border p-4">
        {canRunMatching ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              disabled={matchingState === "running" || !job}
              onClick={() => void runMatching()}
            >
              {matchingState === "running" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Sparkles className="h-4 w-4" aria-hidden="true" />
              )}
              {matchingState === "running" ? "AI matching running" : "Run AI matching"}
            </Button>
          </div>
        ) : null}
        {canRunMatching ? (
          <>
            {/* One line saying what the run is doing right now, derived from
                the milestones the pipeline actually reached. It sits above the
                stage list rather than replacing it: the list is the plan and
                what is left, this is the current work and what it turned up. */}
            <AiActivityIndicator
              activity={matchingActivity}
              errorMessage={
                matchingState === "error" ? matchingMessage : undefined
              }
            />
            <MatchingReasoning
              state={matchingState}
              progress={matchingProgress}
              message={matchingMessage}
            />
          </>
        ) : null}
        {canEmail ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              disabled={inviting || invitable.length === 0}
              onClick={() => void sendInvitations()}
            >
              {inviting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-4 w-4" aria-hidden="true" />
              )}
              Send assessment invitations
              {invitable.length > 0 ? ` (${invitable.length})` : ""}
            </Button>
            <p className="text-xs">
              {selectedRows.length === 0
                ? "Tick candidates below, then return here to send their assessment invitations."
                : `${invitable.length} of ${selectedRows.length} selected can be invited; the rest are already past this stage.`}
            </p>
          </div>
        ) : null}
        {canEmail && sourcedSelected.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              disabled={invitingToApply}
              onClick={() => void inviteToApply()}
            >
              {invitingToApply ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-4 w-4" aria-hidden="true" />
              )}
              Invite to apply ({sourcedSelected.length})
            </Button>
            <p className="text-xs">
              These candidates came from your databank and have not applied.
              This asks them to sign in and apply; nothing enters your pipeline
              until they do.
            </p>
          </div>
        ) : null}
      </div>

      {job ? <PipelineFunnel jobId={jobId} reloadKey={reloadKey} /> : null}

      {/* ── Inline candidate table ──────────────────────────────────────── */}
      <CandidateRankingTable
        jobId={jobId}
        reloadKey={reloadKey}
        onOpenReport={setReportRow}
        onOpenTranscript={setTranscriptRow}
        onEmail={canEmail ? setEmailRows : undefined}
        onSelectionChange={setSelectedRows}
        canDecide={canDecide}
      />

      </div>

      <PPIReportModal
        open={reportRow !== null}
        onOpenChange={(open) => !open && setReportRow(null)}
        linkId={reportRow?.link_id ?? null}
        candidateName={reportRow?.full_name ?? ""}
        jobTitle={job?.title}
      />

      <AssessmentTranscriptModal
        open={transcriptRow !== null}
        onOpenChange={(open) => !open && setTranscriptRow(null)}
        linkId={transcriptRow?.link_id ?? null}
        candidateName={transcriptRow?.full_name ?? ""}
        jobTitle={job?.title}
      />

      <EmailCompositionModal
        open={emailRows.length > 0}
        onOpenChange={(open) => !open && setEmailRows([])}
        candidates={emailRows}
        jobTitle={job?.title ?? ""}
        companyName={company?.company_name ?? "our team"}
        onSent={() => setReloadKey((k) => k + 1)}
      />
    </div>
  );
}

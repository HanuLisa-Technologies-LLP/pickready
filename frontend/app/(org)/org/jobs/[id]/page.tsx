"use client";

// The job detail page (2026-07-27 spec §2/§3/§7). This IS the review screen, 
// there is no separate one any more.
//
//   [ JD, with About Company / Work Life / Benefits, Edit button top-right ]
//   [ RUN AI MATCHING ]
//   [ Assessment setup review: PPI framework + technical questions ]
//   [ Inline candidate table: Name | Level | PRISM Report | Resume | 4 comments ]
//
// Deliberately absent, per the spec: the "Added by HR after ratification"
// metadata, the notes textbox, the approval-status display, and the separate
// JD-edits card. Editing happens in place, in one form.

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
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { asJdLines } from "@/components/job-description";
import { CandidateRankingTable } from "@/components/candidate-ranking-table";
import { DatabankUpload } from "@/components/databank-upload";
import { PipelineFunnel } from "@/components/pipeline-status";
import { PostingWindowBanner } from "@/components/posting-window";
import { EmailCompositionModal } from "@/components/email-composition-modal";
import { JobSetupReview } from "@/components/job-setup-review";
import { PPIReportModal } from "@/components/ppi-report-modal";
import {
  MatchingReasoning,
  type MatchingProgress,
} from "@/components/matching-reasoning";
import { AssessmentTranscriptModal } from "@/components/assessment-transcript";
import { ResumeViewer } from "@/components/resume-viewer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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

/** One labelled JD paragraph; hidden entirely when the field is empty. */
function JdField({ label, value }: { label: string; value: unknown }) {
  const lines = asJdLines(value);
  if (lines.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1 font-semibold">{label}</h4>
      {lines.length === 1 ? (
        <p className="whitespace-pre-line">{lines[0]}</p>
      ) : (
        <ul className="list-disc space-y-1 pl-5">
          {lines.map((line, index) => (
            <li key={`${label}-${index}`}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

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

function experienceLabel(value: unknown): string | null {
  const text = asJdLines(value)[0];
  if (!text) return null;
  if (typeof value === "number") return `${text}+ years`;
  return /years?/i.test(text) ? text : `${text} years`;
}

type Draft = {
  title: string;
  department: string;
  level: string;
  grade: JobGrade | "";
  role: string;
  responsibilities: string;
  accountabilities: string;
  education: string;
  skills: string;
  experience_years: string;
  about_company: string;
  work_life: string;
  benefits: string;
};

function draftFromJob(job: Job): Draft {
  const jd = jobJd(job);
  return {
    title: job.title ?? "",
    department: job.department ?? "",
    level: job.level ?? "",
    grade: job.grade ?? "",
    role: asJdLines(jd.role).join("\n"),
    responsibilities: asJdLines(jd.responsibilities).join("\n"),
    accountabilities: asJdLines(jd.accountabilities).join("\n"),
    education: asJdLines(jd.education).join("\n"),
    skills: (jd.skills ?? []).join(", "),
    experience_years:
      jd.experience_years !== undefined && jd.experience_years !== null
        ? String(jd.experience_years)
        : "",
    // Pre-filled with the RESOLVED value (spec §3.1): opening the editor shows
    // the company's text rather than an empty box, so a recruiter who only
    // wanted to tweak a sentence does not have to retype the paragraph.
    about_company: job.about_company ?? "",
    work_life: job.work_life ?? "",
    benefits: job.benefits ?? "",
  };
}

export default function OrgJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const { toast } = useToast();
  const { hasCapability } = useAuth();

  const canEditJd = hasCapability("edit_job_description");
  const canRunMatching = hasCapability("trigger_matching");
  const canEmail = hasCapability("send_outreach");
  const canDecide = hasCapability("decide_profile");
  const canUploadDatabank = hasCapability("upload_resumes");
  const canRenew = hasCapability("publish_job");

  // Which of the two top-level screens is showing. The JD opens first: a
  // recruiter arriving at a job usually wants to check the posting before the
  // applicants.
  const [tab, setTab] = React.useState<"jd" | "candidates">("jd");

  const [job, setJob] = React.useState<Job | null>(null);
  const [company, setCompany] = React.useState<CompanyProfile | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [saving, setSaving] = React.useState(false);
  // Distinguishes "the job has not arrived yet" from "the job could not be
  // read". Without it a failed load left `job` null forever and the card below
  // showed its loading skeleton for the rest of the session: the job
  // description simply never appeared, and the toast that said why was long
  // gone by the time anyone looked.
  const [jobError, setJobError] = React.useState<string | null>(null);

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
  const [reloadKey, setReloadKey] = React.useState(0);

  const [reportRow, setReportRow] = React.useState<RankedCandidate | null>(null);
  const [transcriptRow, setTranscriptRow] = React.useState<RankedCandidate | null>(null);
  const [resumeRow, setResumeRow] = React.useState<RankedCandidate | null>(null);
  const [emailRows, setEmailRows] = React.useState<RankedCandidate[]>([]);
  const [selectedRows, setSelectedRows] = React.useState<RankedCandidate[]>([]);
  const [inviting, setInviting] = React.useState(false);
  const [renewing, setRenewing] = React.useState(false);


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
      setJob(updated);
      setDraft(draftFromJob(updated));
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
  }, [jobId, toast]);

  const loadJob = React.useCallback(async () => {
    setJobError(null);
    try {
      const res = await apiGet<Job>(`/jobs/${jobId}`);
      setJob(res);
      setDraft(draftFromJob(res));
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
  }, [jobId, toast]);

  React.useEffect(() => {
    void loadJob();
    // The company name is needed for the email placeholders. A failure here is
    // not worth a toast, the modal falls back to a neutral phrase.
    apiGet<CompanyProfile>("/companies/me/profile")
      .then(setCompany)
      .catch(() => setCompany(null));
  }, [loadJob]);

  const saveJd = async () => {
    if (!draft || !job) return;
    setSaving(true);
    try {
      const updated = await apiPatch<Job>(`/jobs/${jobId}`, {
        title: draft.title.trim(),
        department: draft.department.trim() || null,
        level: draft.level.trim() || null,
        ...(draft.grade ? { grade: draft.grade } : {}),
        jd: {
          ...jobJd(job),
          role: draft.role,
          responsibilities: draft.responsibilities,
          accountabilities: draft.accountabilities,
          education: draft.education,
          skills: draft.skills
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          experience_years: (() => {
            const value = draft.experience_years.trim();
            if (!value) return null;
            const numeric = Number(value);
            return Number.isFinite(numeric) ? numeric : value;
          })(),
        },
        // Sending null (not "") clears the per-job override so the section
        // falls back to the company profile, the two mean different things.
        about_company: draft.about_company.trim() || null,
        work_life: draft.work_life.trim() || null,
        benefits: draft.benefits.trim() || null,
      });
      setJob(updated);
      setDraft(draftFromJob(updated));
      setEditing(false);
      toast({ title: "Job description updated" });
    } catch (e) {
      toast({
        title: "Couldn't save the job description",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  // Only applicants still at `applied` can be invited; the backend refuses the
  // rest and reports them, but filtering here keeps the button honest about
  // how many it will actually send.
  const invitable = selectedRows.filter((r) => r.status === "applied");

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
    setMatchingMessage("Starting the run.");
    try {
      const res = await apiPost<{ candidate_count: number; task_id: string }>(
        `/jobs/${jobId}/run-matching`
      );
      let finished = false;
      let finalState = "PENDING";
      for (let attempt = 0; attempt < 240; attempt += 1) {
        const status = await apiGet<MatchingTaskStatus>(
          `/matching/tasks/${res.task_id}`
        );
        finalState = status.state;
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
            ? [
                job.department,
                // The experience band replaced the old free-text level.
                job.experience_min_years !== null &&
                job.experience_min_years !== undefined &&
                job.experience_max_years !== null &&
                job.experience_max_years !== undefined
                  ? `${job.experience_min_years} to ${job.experience_max_years} years`
                  : job.level,
                job.requirement_period,
              ]
                .filter(Boolean)
                .join(" · ")
            : undefined
        }
        actions={
          job ? (
            <Badge variant="secondary">Level: {jobGradeLabel(job.grade)}</Badge>
          ) : undefined
        }
      />

      {job ? (
        <PostingWindowBanner
          job={job}
          className="mb-6"
          onRenew={canRenew ? () => void renewPosting() : undefined}
          renewing={renewing}
        />
      ) : null}

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

      {/* ── Job description ─────────────────────────────────────────────── */}
      <Card className={cn("mb-6", tab !== "jd" && "hidden")}>
        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Job description</CardTitle>
            <CardDescription>
              Reporting to {String(jd.reporting_to || "-")}
              {jd.reportees ? ` · Reportees: ${jd.reportees}` : ""}
            </CardDescription>
          </div>
          {canEditJd && !editing ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditing(true)}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
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
          ) : editing && draft ? (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Title" htmlFor="jd-title" required>
                  <Input
                    id="jd-title"
                    value={draft.title}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  />
                </FormField>
                <FormField label="Department" htmlFor="jd-dept">
                  <Input
                    id="jd-dept"
                    value={draft.department}
                    onChange={(e) =>
                      setDraft({ ...draft, department: e.target.value })
                    }
                  />
                </FormField>
                <FormField
                  label="Level"
                  htmlFor="jd-grade"
                  hint="Decides which assessment applicants receive."
                >
                  <Select
                    value={draft.grade}
                    onValueChange={(v) => setDraft({ ...draft, grade: v as JobGrade })}
                  >
                    <SelectTrigger id="jd-grade">
                      <SelectValue placeholder="Select a level" />
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

              <FormField label="Role" htmlFor="jd-role">
                <Textarea
                  id="jd-role"
                  rows={2}
                  value={draft.role}
                  onChange={(e) => setDraft({ ...draft, role: e.target.value })}
                />
              </FormField>
              <FormField label="Responsibilities" htmlFor="jd-resp">
                <Textarea
                  id="jd-resp"
                  rows={4}
                  value={draft.responsibilities}
                  onChange={(e) =>
                    setDraft({ ...draft, responsibilities: e.target.value })
                  }
                />
              </FormField>
              <FormField label="Accountabilities" htmlFor="jd-acc">
                <Textarea
                  id="jd-acc"
                  rows={3}
                  value={draft.accountabilities}
                  onChange={(e) =>
                    setDraft({ ...draft, accountabilities: e.target.value })
                  }
                />
              </FormField>
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField label="Education" htmlFor="jd-edu">
                  <Input
                    id="jd-edu"
                    value={draft.education}
                    onChange={(e) =>
                      setDraft({ ...draft, education: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Skills (comma-separated)" htmlFor="jd-skills">
                  <Input
                    id="jd-skills"
                    value={draft.skills}
                    onChange={(e) => setDraft({ ...draft, skills: e.target.value })}
                  />
                </FormField>
                <FormField label="Experience (years)" htmlFor="jd-exp">
                  <Input
                    id="jd-exp"
                    placeholder="e.g. 3 or 3-5"
                    value={draft.experience_years}
                    onChange={(e) =>
                      setDraft({ ...draft, experience_years: e.target.value })
                    }
                  />
                </FormField>
              </div>

              <FormField
                label="About company"
                htmlFor="jd-about"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="jd-about"
                  rows={4}
                  value={draft.about_company}
                  onChange={(e) =>
                    setDraft({ ...draft, about_company: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="Work life"
                htmlFor="jd-worklife"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="jd-worklife"
                  rows={4}
                  value={draft.work_life}
                  onChange={(e) => setDraft({ ...draft, work_life: e.target.value })}
                />
              </FormField>
              <FormField
                label="Benefits"
                htmlFor="jd-benefits"
                hint="Defaults to your company profile. Editing it here changes this job only."
              >
                <Textarea
                  id="jd-benefits"
                  rows={4}
                  value={draft.benefits}
                  onChange={(e) => setDraft({ ...draft, benefits: e.target.value })}
                />
              </FormField>

              <div className="flex gap-2">
                <Button
                  disabled={saving || !draft.title.trim()}
                  onClick={() => void saveJd()}
                >
                  {saving ? "Saving" : "Save changes"}
                </Button>
                <Button
                  variant="outline"
                  disabled={saving}
                  onClick={() => {
                    setDraft(draftFromJob(job));
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <JdField label="Description" value={jd.description} />
              <JdField label="Role" value={jd.role} />
              <JdField label="Responsibilities" value={jd.responsibilities} />
              <JdField label="Accountabilities" value={jd.accountabilities} />
              <JdField label="Education" value={jd.education} />
              <JdField label="Experience" value={experienceLabel(jd.experience_years)} />
              <div>
                <h4 className="mb-1 font-semibold">Skills</h4>
                <div className="flex flex-wrap gap-1.5">
                  {(jd.skills ?? []).length > 0 ? (
                    (jd.skills ?? []).map((s) => (
                      <Badge key={s} variant="secondary">
                        {s}
                      </Badge>
                    ))
                  ) : (
                    <span>-</span>
                  )}
                </div>
              </div>
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
      {/* ── Assessment selection (spec §3.1) ─────────────────────────────── */}
      {/* The one manual step (spec §11): the PPI framework is reviewed and
          saved here before any candidate can be invited. Technical questions
          are no longer shown -- they are written per candidate during the
          assessment, and what each person was actually asked is on their own
          row in the table below. */}
      {job ? <JobSetupReview jobId={jobId} /> : null}

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
          <MatchingReasoning
            state={matchingState}
            progress={matchingProgress}
            message={matchingMessage}
          />
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
            <p className="text-xs leading-5">
              {selectedRows.length === 0
                ? "Tick candidates below, then return here to send their assessment invitations."
                : `${invitable.length} of ${selectedRows.length} selected can be invited; the rest are already past this stage.`}
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
        onOpenResume={setResumeRow}
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

      <ResumeViewer
        open={resumeRow !== null}
        onOpenChange={(open) => !open && setResumeRow(null)}
        resumeUrl={resumeRow?.resume_url}
        profileId={resumeRow?.profile_id}
        resumeFileName={resumeRow?.resume_filename}
        resumeMimeType={resumeRow?.resume_mime_type}
        candidateName={resumeRow?.full_name ?? ""}
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

"use client";

// HR Review Screen (FR-7.1, nav-gated by `view_review_screen`): candidate
// names on the left; the selected candidate's assessment and resume on the right.
// The candidate links (from GET /candidates/jobs/{job_id}) now carry the
// 4-parameter `breakdown` (rev 2), surfaced as comments only.
//
// Sourcing lives here too: "Upload freshly sourced resume" moved off the job
// detail page (the per-job Matching & Sourcing tab was removed) and reuses this
// screen's single job selector rather than introducing a second one.

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  Sparkles,
  Upload,
  UserCheck,
  Users,
} from "lucide-react";

import { apiGet, apiPost, apiUploadWithProgress } from "@/lib/api";
import {
  MatchingReasoning,
  type MatchingProgress,
} from "@/components/matching-reasoning";
import type { CandidateLink, Job, MatchingTaskStatus } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { ProfileReview } from "@/components/profile-review";
import { HmDecisionActions } from "@/components/hm-decision-actions";
import { ResumeFileInput } from "@/components/resume-file-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
} from "@/components/page-primitives";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function OrgReviewScreen() {
  const { toast } = useToast();
  const { hasCapability } = useAuth();
  // A Hiring Manager acts on profiles (FR-8.2); HR grants access (FR-8.1).
  const canDecide = hasCapability("decide_profile");
  const canGrant = hasCapability("view_review_screen") && !canDecide;
  const canSendOutreach = hasCapability("send_outreach");
  const canTriggerMatching = hasCapability("trigger_matching");
  // Sourcing gates carried over verbatim from the removed per-job tab.
  const canUpload = hasCapability("upload_resumes");
  const canViewDatabank = hasCapability("view_databank");
  const showSourcing = canUpload || canViewDatabank || canTriggerMatching;
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [jobId, setJobId] = React.useState<string>("");
  const [links, setLinks] = React.useState<CandidateLink[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [matchingTaskId, setMatchingTaskId] = React.useState<string | null>(null);
  const [matchingCount, setMatchingCount] = React.useState(0);
  const [matchingProgress, setMatchingProgress] =
    React.useState<MatchingProgress | null>(null);

  // Freshly sourced resume upload (moved from the job detail page).
  const [sourcingOpen, setSourcingOpen] = React.useState(false);
  const [file, setFile] = React.useState<File | null>(null);
  const [uploadForm, setUploadForm] = React.useState({
    email: "",
    full_name: "",
    phone: "",
  });
  const [uploading, setUploading] = React.useState(false);
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [uploadError, setUploadError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<Job[] | { jobs: Job[] }>("/jobs")
      .then((res) => {
        const all = Array.isArray(res) ? res : res.jobs ?? [];
        setJobs(all);
        if (all.length > 0) setJobId(all[0].id);
      })
      .catch(() => {});
  }, []);

  const loadLinks = React.useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const res = await apiGet<CandidateLink[] | { links: CandidateLink[] }>(
        `/candidates/jobs/${jobId}`
      );
      setLinks(Array.isArray(res) ? res : res.links ?? []);
    } catch (e) {
      setLinks([]);
      setLoadError(
        e instanceof Error
          ? e.message
          : "Could not load candidates for this job."
      );
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  React.useEffect(() => {
    if (!matchingTaskId) return;
    const timer = window.setInterval(() => {
      void apiGet<MatchingTaskStatus>(`/matching/tasks/${matchingTaskId}`)
        .then((status) => {
          // The stage list, same source as the job page. Shown inline under the
          // header rather than behind a spinner on the button, so the recruiter
          // can see which step the run is on and whether anything degraded.
          if (status.stages?.length) {
            setMatchingProgress({
              stages: status.stages,
              candidate_count: status.candidate_count ?? 0,
              scored_count: status.scored_count ?? 0,
            });
          }
          if (!status.done) return;
          window.clearInterval(timer);
          setMatchingTaskId(null);
          void loadLinks();
          toast({
            title: "AI matching complete",
            description: `${matchingCount} candidates were scored.`,
          });
        })
        .catch((error) => {
          window.clearInterval(timer);
          setMatchingTaskId(null);
          toast({
            title: "Could not read matching progress",
            description: error instanceof Error ? error.message : undefined,
            variant: "destructive",
          });
        });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadLinks, matchingCount, matchingTaskId, toast]);

  const runMatching = async () => {
    if (!jobId) return;
    try {
      const result = await apiPost<{
        task_id: string;
        candidate_count: number;
      }>(`/matching/jobs/${jobId}/run`);
      setMatchingCount(result.candidate_count);
      setMatchingProgress(null);
      setMatchingTaskId(result.task_id);
    } catch (error) {
      toast({
        title: "Could not run AI matching",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    }
  };

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobId || !file || !uploadForm.email) return;
    setUploading(true);
    setUploadProgress(0);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("email", uploadForm.email);
      if (uploadForm.full_name) fd.append("full_name", uploadForm.full_name);
      if (uploadForm.phone) fd.append("phone", uploadForm.phone);
      await apiUploadWithProgress(
        `/candidates/jobs/${jobId}/upload-resume`,
        fd,
        setUploadProgress
      );
      toast({
        title: "Resume uploaded",
        description:
          "Candidate linked as freshly sourced; parsing has been queued.",
      });
      setFile(null);
      setUploadForm({ email: "", full_name: "", phone: "" });
      void loadLinks();
    } catch (err) {
      setUploadError(
        err instanceof Error ? err.message : "Upload failed. Please retry."
      );
      toast({
        title: "Upload failed",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  const grantAccess = async (link: CandidateLink) => {
    setBusy(true);
    try {
      await apiPost(`/candidates/links/${link.link_id}/grant-access`);
      toast({
        title: "Hiring Manager access granted",
        description: link.candidate.full_name || link.candidate.email,
      });
      void loadLinks();
    } catch (e) {
      toast({
        title: "Grant failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Review candidates"
        description="Review each candidate's AI assessment and resume, then move the strongest profiles forward."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {canTriggerMatching ? (
              <Button
                onClick={() => void runMatching()}
                disabled={!jobId || Boolean(matchingTaskId)}
              >
                {matchingTaskId ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="h-4 w-4" aria-hidden="true" />
                )}
                {matchingTaskId ? "Scoring candidates" : "Run AI matching"}
              </Button>
            ) : null}
            <Select value={jobId} onValueChange={setJobId}>
              <SelectTrigger className="w-full sm:w-64">
                <SelectValue placeholder="Select a job" />
              </SelectTrigger>
              <SelectContent>
                {jobs.map((j) => (
                  <SelectItem key={j.id} value={j.id}>
                    {j.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        }
      />

      {/* Inline, so a run in progress does not take the page away from the
          recruiter and the degraded paths announce themselves. */}
      {matchingProgress ? (
        <MatchingReasoning
          className="mb-6"
          state={matchingTaskId ? "running" : "done"}
          progress={matchingProgress}
          message=""
        />
      ) : null}

      {showSourcing && jobId ? (
        <Card className="mb-6">
          <CardHeader>
            <button
              type="button"
              className="flex w-full items-start justify-between gap-3 text-left"
              aria-expanded={sourcingOpen}
              onClick={() => setSourcingOpen((open) => !open)}
            >
              <div className="min-w-0">
                <h2 className="text-base font-semibold">Source a candidate</h2>
                <p className="mt-1 text-sm leading-6">
                  Upload a resume against{" "}
                  {jobs.find((j) => j.id === jobId)?.title ?? "the selected job"}.
                </p>
              </div>
              {sourcingOpen ? (
                <ChevronDown className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
              ) : (
                <ChevronRight className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
              )}
            </button>
          </CardHeader>
          {sourcingOpen ? (
            <CardContent>
              {canUpload ? (
                <form className="max-w-xl space-y-4" onSubmit={upload}>
                  <FormField
                    label="Resume file"
                    htmlFor="resume-file"
                    required
                    hint="PDF or DOCX, up to 10 MB."
                  >
                    <ResumeFileInput
                      id="resume-file"
                      file={file}
                      progress={uploadProgress}
                      error={uploadError}
                      disabled={uploading}
                      onFileChange={(nextFile, validationError) => {
                        setFile(nextFile);
                        setUploadError(validationError);
                        setUploadProgress(0);
                      }}
                      onRetry={() => {
                        if (file && uploadForm.email) {
                          void upload({
                            preventDefault() {},
                          } as React.FormEvent);
                        }
                      }}
                    />
                  </FormField>
                  <FormField label="Candidate email" htmlFor="cand-email" required>
                    <Input
                      id="cand-email"
                      type="email"
                      value={uploadForm.email}
                      onChange={(e) =>
                        setUploadForm({ ...uploadForm, email: e.target.value })
                      }
                      required
                    />
                  </FormField>
                  <FormField label="Candidate name" htmlFor="cand-name">
                    <Input
                      id="cand-name"
                      value={uploadForm.full_name}
                      onChange={(e) =>
                        setUploadForm({
                          ...uploadForm,
                          full_name: e.target.value,
                        })
                      }
                    />
                  </FormField>
                  <FormField label="Phone" htmlFor="cand-phone">
                    <Input
                      id="cand-phone"
                      type="tel"
                      value={uploadForm.phone}
                      onChange={(e) =>
                        setUploadForm({ ...uploadForm, phone: e.target.value })
                      }
                    />
                  </FormField>
                  <Button
                    type="submit"
                    disabled={uploading || !file || !uploadForm.email}
                  >
                    <Upload className="h-4 w-4" aria-hidden="true" />
                    {uploading ? "Uploading" : "Upload and link"}
                  </Button>
                </form>
              ) : (
                <p className="text-sm leading-6">
                  You can review sourced candidates here. Uploading a resume
                  needs the resume upload permission.
                </p>
              )}
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      {loading ? (
        <LoadingRows rows={5} label="Loading candidates" />
      ) : loadError ? (
        <ErrorState
          title="Could not load candidates"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void loadLinks()}>
              Try again
            </Button>
          }
        />
      ) : !jobId ? (
        <EmptyState
          icon={Users}
          title="Pick a job"
          description="Choose a job above to review the people who applied to it."
        />
      ) : (
        <ProfileReview
          links={links}
          jobId={jobId}
          canSendOutreach={canSendOutreach}
          emptyMessage="No candidates linked to this job yet."
          renderActions={(link) => {
            // Hiring Manager: act on profiles they've been granted (FR-8.2).
            if (canDecide) {
              return link.hm_access_granted ? (
                <HmDecisionActions link={link} onDecided={() => void loadLinks()} />
              ) : null;
            }
            // HR: grant Hiring Manager access to a reviewed profile (FR-8.1).
            if (canGrant) {
              return link.hm_access_granted ? null : (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => void grantAccess(link)}
                >
                  <UserCheck className="h-4 w-4" aria-hidden="true" /> Grant
                  Hiring Manager access
                </Button>
              );
            }
            return null;
          }}
        />
      )}
    </div>
  );
}

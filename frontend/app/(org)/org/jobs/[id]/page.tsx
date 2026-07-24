"use client";

// Unified org job workspace (contract rev 2) — merges the former HR and
// Recruiter job pages into one, with individual actions capability-gated:
//  - Detail tab: JD summary + compensation editor + JD edits (FR-4.1)
//  - Matching & Sourcing tab: resume upload (upload_resumes), trigger
//    matching (trigger_matching), 4-parameter results, outreach send
//    (send_outreach) — Databank candidates skip outreach
//  - Candidates tab: pipeline status (update_pipeline_status), interview
//    scheduling (schedule_interviews), HM access grant, verification view
//    + HR override

import * as React from "react";
import { useParams } from "next/navigation";
import {
  CalendarPlus,
  Send,
  ShieldQuestion,
  Upload,
  UserCheck,
} from "lucide-react";

import { apiGet, apiPost, apiPut, apiUploadWithProgress } from "@/lib/api";
import type { CandidateLink, Job, VerificationRequest } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { MatchingResults } from "@/components/matching-results";
import { StatusBadge } from "@/components/status-badge";
import { TierBadge } from "@/components/tier-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField, FormSection } from "@/components/ui/form";
import { ResumeFileInput } from "@/components/resume-file-input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PIPELINE_STATUSES = ["rejected", "shortlisted", "offered", "joined"] as const;

export default function OrgJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const { toast } = useToast();
  const { hasCapability } = useAuth();

  const canUpload = hasCapability("upload_resumes");
  const canTrigger = hasCapability("trigger_matching");
  const canOutreach = hasCapability("send_outreach");
  const canViewDatabank = hasCapability("view_databank");
  const canUpdatePipeline = hasCapability("update_pipeline_status");
  const canScheduleInterviews = hasCapability("schedule_interviews");
  // ASSUMPTION: granting Hiring-Manager access and verification overrides are
  // HR actions; `view_review_screen` is the closest contract capability. The
  // server enforces the real RBAC either way.
  const canGrantAccess = hasCapability("view_review_screen");
  const showMatchingTab =
    canViewDatabank || canUpload || canTrigger || canOutreach;

  const [job, setJob] = React.useState<Job | null>(null);
  const [links, setLinks] = React.useState<CandidateLink[]>([]);

  // Compensation + JD edit state
  const [compensation, setCompensation] = React.useState({
    ctc_min: "",
    ctc_max: "",
    currency: "INR",
    notes: "",
  });
  const [jdDraft, setJdDraft] = React.useState({
    responsibilities: "",
    accountabilities: "",
    education: "",
    skills: "",
    experience_years: "",
  });
  const [savingComp, setSavingComp] = React.useState(false);
  const [savingJd, setSavingJd] = React.useState(false);

  // Resume upload
  const [file, setFile] = React.useState<File | null>(null);
  const [uploadForm, setUploadForm] = React.useState({
    email: "",
    full_name: "",
    phone: "",
  });
  const [uploading, setUploading] = React.useState(false);
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [uploadError, setUploadError] = React.useState<string | null>(null);

  // Outreach selection (fresh candidates only)
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [sendingOutreach, setSendingOutreach] = React.useState(false);

  // Interview scheduling
  const [interviewLink, setInterviewLink] = React.useState<CandidateLink | null>(null);
  const [interviewForm, setInterviewForm] = React.useState({
    scheduled_at: "",
    notes: "",
  });
  const [scheduling, setScheduling] = React.useState(false);

  // Verification dialog
  const [verifLink, setVerifLink] = React.useState<CandidateLink | null>(null);
  const [verifRequests, setVerifRequests] = React.useState<VerificationRequest[]>([]);
  const [overrideTarget, setOverrideTarget] =
    React.useState<VerificationRequest | null>(null);
  const [overrideReason, setOverrideReason] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const loadJob = React.useCallback(async () => {
    try {
      const res = await apiGet<Job | { job: Job }>(`/jobs/${jobId}`);
      const j = "job" in (res as object) && (res as { job?: Job }).job
        ? (res as { job: Job }).job
        : (res as Job);
      setJob(j);
      const comp = (j.compensation ?? {}) as Record<string, unknown>;
      setCompensation({
        ctc_min: comp.ctc_min !== undefined ? String(comp.ctc_min) : "",
        ctc_max: comp.ctc_max !== undefined ? String(comp.ctc_max) : "",
        currency: typeof comp.currency === "string" ? comp.currency : "INR",
        notes: typeof comp.notes === "string" ? comp.notes : "",
      });
      setJdDraft({
        responsibilities: j.jd?.responsibilities ?? "",
        accountabilities: j.jd?.accountabilities ?? "",
        education: j.jd?.education ?? "",
        skills: (j.jd?.skills ?? []).join(", "),
        experience_years: String(j.jd?.experience_years ?? ""),
      });
    } catch (e) {
      toast({
        title: "Failed to load job",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    }
  }, [jobId, toast]);

  const loadLinks = React.useCallback(async () => {
    try {
      const res = await apiGet<CandidateLink[] | { links: CandidateLink[] }>(
        `/candidates/jobs/${jobId}`
      );
      setLinks(Array.isArray(res) ? res : res.links ?? []);
    } catch {
      setLinks([]);
    }
  }, [jobId]);

  React.useEffect(() => {
    void loadJob();
    void loadLinks();
  }, [loadJob, loadLinks]);

  const saveCompensation = async () => {
    setSavingComp(true);
    try {
      await apiPut(`/jobs/${jobId}/compensation`, {
        compensation: {
          ctc_min: compensation.ctc_min ? Number(compensation.ctc_min) : null,
          ctc_max: compensation.ctc_max ? Number(compensation.ctc_max) : null,
          currency: compensation.currency,
          notes: compensation.notes,
        },
      });
      toast({ title: "Compensation saved" });
    } catch (e) {
      toast({
        title: "Save failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSavingComp(false);
    }
  };

  const saveJd = async () => {
    if (!job) return;
    setSavingJd(true);
    try {
      await apiPut(`/jobs/${jobId}/jd`, {
        jd: {
          ...job.jd,
          responsibilities: jdDraft.responsibilities,
          accountabilities: jdDraft.accountabilities,
          education: jdDraft.education,
          skills: jdDraft.skills
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          experience_years: Number(jdDraft.experience_years) || 0,
        },
      });
      toast({ title: "JD updated" });
      void loadJob();
    } catch (e) {
      toast({
        title: "JD update failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSavingJd(false);
    }
  };

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !uploadForm.email) return;
    setUploading(true);
    setUploadProgress(0);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("email", uploadForm.email);
      if (uploadForm.full_name) fd.append("full_name", uploadForm.full_name);
      if (uploadForm.phone) fd.append("phone", uploadForm.phone);
      await apiUploadWithProgress(`/candidates/jobs/${jobId}/upload-resume`, fd, setUploadProgress);
      toast({
        title: "Resume uploaded",
        description:
          "Candidate linked as freshly sourced; parsing has been queued.",
      });
      setFile(null);
      setUploadForm({ email: "", full_name: "", phone: "" });
      void loadLinks();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed. Please retry.");
      toast({
        title: "Upload failed",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  const toggleSelect = (candidateId: string, source: string) => {
    if (source !== "fresh") return; // Databank candidates skip outreach
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(candidateId)) next.delete(candidateId);
      else next.add(candidateId);
      return next;
    });
  };

  const sendOutreach = async () => {
    setSendingOutreach(true);
    try {
      await apiPost("/verification/outreach", {
        job_id: jobId,
        candidate_ids: Array.from(selected),
      });
      toast({
        title: "Outreach sent",
        description: `${selected.size} candidate(s) will receive the 40-aspect + verification request.`,
      });
      setSelected(new Set());
    } catch (e) {
      toast({
        title: "Outreach failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSendingOutreach(false);
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

  const updateStatus = async (link: CandidateLink, status: string) => {
    try {
      await apiPost(`/candidates/links/${link.link_id}/status`, { status });
      toast({
        title: "Status updated",
        description: `${link.candidate.full_name || link.candidate.email} → ${status}`,
      });
      void loadLinks();
    } catch (e) {
      toast({
        title: "Status update failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    }
  };

  const schedule = async () => {
    if (!interviewLink || !interviewForm.scheduled_at) return;
    setScheduling(true);
    try {
      await apiPost(`/candidates/links/${interviewLink.link_id}/interviews`, {
        scheduled_at: new Date(interviewForm.scheduled_at).toISOString(),
        ...(interviewForm.notes ? { notes: interviewForm.notes } : {}),
      });
      toast({
        title: "Interview scheduled",
        description:
          "The invite (.ics) is sent from the client's verified domain.",
      });
      setInterviewLink(null);
      setInterviewForm({ scheduled_at: "", notes: "" });
    } catch (e) {
      toast({
        title: "Scheduling failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setScheduling(false);
    }
  };

  const openVerification = async (link: CandidateLink) => {
    setVerifLink(link);
    setVerifRequests([]);
    if (!link.profile_id) return;
    try {
      const res = await apiGet<
        VerificationRequest[] | { requests: VerificationRequest[] }
      >(`/verification/profile/${link.profile_id}`);
      setVerifRequests(Array.isArray(res) ? res : res.requests ?? []);
    } catch {
      setVerifRequests([]);
    }
  };

  const submitOverride = async () => {
    if (!overrideTarget || !overrideReason.trim()) return;
    setBusy(true);
    try {
      await apiPost(`/verification/requests/${overrideTarget.id}/override`, {
        reason: overrideReason.trim(),
      });
      toast({ title: "Verification overridden", description: "Audit-logged." });
      setOverrideTarget(null);
      setOverrideReason("");
      if (verifLink) void openVerification(verifLink);
    } catch (e) {
      toast({
        title: "Override failed",
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
        title={job?.title ?? "Job"}
        description={
          job
            ? `${job.department} · ${job.level} · ${job.requirement_period}`
            : undefined
        }
        actions={job ? <StatusBadge status={job.status} /> : undefined}
      />

      <Tabs defaultValue="detail">
        <TabsList>
          <TabsTrigger value="detail">Detail & JD</TabsTrigger>
          {showMatchingTab ? (
            <TabsTrigger value="matching">Matching & Sourcing</TabsTrigger>
          ) : null}
          <TabsTrigger value="candidates">Candidates</TabsTrigger>
        </TabsList>

        <TabsContent value="detail">
          <div className="space-y-6">
            {job ? (
              <Card>
                <CardHeader>
                  <CardTitle>Job description</CardTitle>
                  <CardDescription>
                    Reporting to {job.jd?.reporting_to || "—"}
                    {job.jd?.reportees ? ` · Reportees: ${job.jd.reportees}` : ""}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div>
                    <h4 className="mb-1 font-semibold">Role</h4>
                    <p className="text-muted-foreground">{job.jd?.role || "—"}</p>
                  </div>
                  <div>
                    <h4 className="mb-1 font-semibold">Skills</h4>
                    <div className="flex flex-wrap gap-1.5">
                      {(job.jd?.skills ?? []).length > 0 ? (
                        (job.jd?.skills ?? []).map((s) => (
                          <Badge key={s} variant="secondary">
                            {s}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ) : null}

            {/* ASSUMPTION: no dedicated capability exists in the contract for
                compensation/JD edits; the editors are shown to org users and
                the backend enforces the real permission (HR, post-ratification). */}
            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardContent className="space-y-4 pt-6">
                  <FormSection
                    title="Compensation"
                    description="Added by HR after ratification (FR-4.1)."
                  >
                    <div className="grid gap-4 sm:grid-cols-2">
                      <FormField label="CTC min" htmlFor="ctc-min">
                        <Input
                          id="ctc-min"
                          type="number"
                          value={compensation.ctc_min}
                          onChange={(e) =>
                            setCompensation({
                              ...compensation,
                              ctc_min: e.target.value,
                            })
                          }
                        />
                      </FormField>
                      <FormField label="CTC max" htmlFor="ctc-max">
                        <Input
                          id="ctc-max"
                          type="number"
                          value={compensation.ctc_max}
                          onChange={(e) =>
                            setCompensation({
                              ...compensation,
                              ctc_max: e.target.value,
                            })
                          }
                        />
                      </FormField>
                    </div>
                    <FormField label="Currency" htmlFor="currency">
                      <Input
                        id="currency"
                        value={compensation.currency}
                        onChange={(e) =>
                          setCompensation({
                            ...compensation,
                            currency: e.target.value,
                          })
                        }
                      />
                    </FormField>
                    <FormField label="Notes" htmlFor="comp-notes">
                      <Textarea
                        id="comp-notes"
                        rows={2}
                        value={compensation.notes}
                        onChange={(e) =>
                          setCompensation({
                            ...compensation,
                            notes: e.target.value,
                          })
                        }
                      />
                    </FormField>
                    <Button
                      onClick={() => void saveCompensation()}
                      disabled={savingComp}
                    >
                      {savingComp ? "Saving…" : "Save compensation"}
                    </Button>
                  </FormSection>
                </CardContent>
              </Card>

              <Card>
                <CardContent className="space-y-4 pt-6">
                  <FormSection
                    title="JD edits"
                    description="Resolve JD ambiguity post-ratification."
                  >
                    <FormField label="Responsibilities" htmlFor="jd-resp">
                      <Textarea
                        id="jd-resp"
                        rows={3}
                        value={jdDraft.responsibilities}
                        onChange={(e) =>
                          setJdDraft({
                            ...jdDraft,
                            responsibilities: e.target.value,
                          })
                        }
                      />
                    </FormField>
                    <FormField label="Accountabilities" htmlFor="jd-acc">
                      <Textarea
                        id="jd-acc"
                        rows={3}
                        value={jdDraft.accountabilities}
                        onChange={(e) =>
                          setJdDraft({
                            ...jdDraft,
                            accountabilities: e.target.value,
                          })
                        }
                      />
                    </FormField>
                    <FormField label="Education" htmlFor="jd-edu">
                      <Input
                        id="jd-edu"
                        value={jdDraft.education}
                        onChange={(e) =>
                          setJdDraft({ ...jdDraft, education: e.target.value })
                        }
                      />
                    </FormField>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <FormField label="Skills (comma-separated)" htmlFor="jd-skills">
                        <Input
                          id="jd-skills"
                          value={jdDraft.skills}
                          onChange={(e) =>
                            setJdDraft({ ...jdDraft, skills: e.target.value })
                          }
                        />
                      </FormField>
                      <FormField label="Experience (years)" htmlFor="jd-exp">
                        <Input
                          id="jd-exp"
                          type="number"
                          value={jdDraft.experience_years}
                          onChange={(e) =>
                            setJdDraft({
                              ...jdDraft,
                              experience_years: e.target.value,
                            })
                          }
                        />
                      </FormField>
                    </div>
                    <Button onClick={() => void saveJd()} disabled={savingJd}>
                      {savingJd ? "Saving…" : "Save JD"}
                    </Button>
                  </FormSection>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        {showMatchingTab ? (
          <TabsContent value="matching">
            <div className="space-y-6">
              {canUpload ? (
                <Card className="max-w-xl">
                  <CardHeader>
                    <CardTitle>Upload freshly sourced resume</CardTitle>
                    <CardDescription>
                      Creates the candidate + profile, links it to this job as
                      fresh-sourced, and queues LLM resume parsing.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <form className="space-y-4" onSubmit={upload}>
                      <FormField label="Resume file" htmlFor="resume-file" required>
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
                              void upload({ preventDefault() {} } as React.FormEvent);
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
                        className="gap-2"
                      >
                        <Upload className="h-4 w-4" />
                        {uploading ? "Uploading…" : "Upload & link"}
                      </Button>
                    </form>
                  </CardContent>
                </Card>
              ) : null}

              {canOutreach ? (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    Select fresh candidates below and send the 40-aspect +
                    employer-verification outreach. Databank matches skip this
                    flow.
                  </p>
                  <Button
                    className="gap-2"
                    disabled={selected.size === 0 || sendingOutreach}
                    onClick={() => void sendOutreach()}
                  >
                    <Send className="h-4 w-4" />
                    {sendingOutreach
                      ? "Sending…"
                      : `Send outreach (${selected.size})`}
                  </Button>
                </div>
              ) : null}
              <MatchingResults
                jobId={jobId}
                selectable={canOutreach}
                selected={selected}
                onToggleSelect={toggleSelect}
                canTrigger={canTrigger}
              />
            </div>
          </TabsContent>
        ) : null}

        <TabsContent value="candidates">
          <p className="mb-4 text-sm text-muted-foreground">
            Keeping every profile&apos;s status current is mandatory (FR-8.4).
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Candidate</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Verification</TableHead>
                {canUpdatePipeline ? <TableHead>Pipeline update</TableHead> : null}
                {canScheduleInterviews ? (
                  <TableHead>Interview</TableHead>
                ) : null}
                {canGrantAccess ? (
                  <TableHead className="text-right">HM access</TableHead>
                ) : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {links.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={8}
                    className="text-center text-muted-foreground"
                  >
                    No candidates linked to this job yet.
                  </TableCell>
                </TableRow>
              ) : (
                links.map((link) => (
                  <TableRow key={link.link_id}>
                    <TableCell className="font-medium">
                      {link.candidate.full_name || link.candidate.email}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="capitalize">
                        {link.source}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <TierBadge tier={link.tier} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={link.status} />
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="gap-1"
                        onClick={() => void openVerification(link)}
                      >
                        <ShieldQuestion className="h-4 w-4" /> View
                      </Button>
                    </TableCell>
                    {canUpdatePipeline ? (
                      <TableCell>
                        <Select
                          onValueChange={(v) => void updateStatus(link, v)}
                        >
                          <SelectTrigger className="h-8 w-36">
                            <SelectValue placeholder="Set status" />
                          </SelectTrigger>
                          <SelectContent>
                            {PIPELINE_STATUSES.map((s) => (
                              <SelectItem key={s} value={s} className="capitalize">
                                {s}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                    ) : null}
                    {canScheduleInterviews ? (
                      <TableCell>
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1"
                          onClick={() => setInterviewLink(link)}
                        >
                          <CalendarPlus className="h-4 w-4" /> Schedule
                        </Button>
                      </TableCell>
                    ) : null}
                    {canGrantAccess ? (
                      <TableCell className="text-right">
                        {link.hm_access_granted ? (
                          <Badge variant="secondary">Granted</Badge>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            disabled={busy}
                            onClick={() => void grantAccess(link)}
                          >
                            <UserCheck className="h-4 w-4" /> Grant
                          </Button>
                        )}
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TabsContent>
      </Tabs>

      {/* Interview scheduling dialog */}
      <Dialog
        open={interviewLink !== null}
        onOpenChange={(open) => {
          if (!open) {
            setInterviewLink(null);
            setInterviewForm({ scheduled_at: "", notes: "" });
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Schedule interview —{" "}
              {interviewLink?.candidate.full_name ||
                interviewLink?.candidate.email}
            </DialogTitle>
            <DialogDescription>
              The invite with an .ics attachment is sent from the client&apos;s
              own verified email domain — never Gmail/Outlook.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <FormField label="Date & time" htmlFor="int-dt" required>
              <Input
                id="int-dt"
                type="datetime-local"
                value={interviewForm.scheduled_at}
                onChange={(e) =>
                  setInterviewForm({
                    ...interviewForm,
                    scheduled_at: e.target.value,
                  })
                }
              />
            </FormField>
            <FormField label="Notes" htmlFor="int-notes">
              <Textarea
                id="int-notes"
                rows={3}
                placeholder="Round details, panel, meeting link…"
                value={interviewForm.notes}
                onChange={(e) =>
                  setInterviewForm({ ...interviewForm, notes: e.target.value })
                }
              />
            </FormField>
          </div>
          <DialogFooter>
            <Button
              disabled={scheduling || !interviewForm.scheduled_at}
              onClick={() => void schedule()}
            >
              {scheduling ? "Scheduling…" : "Schedule & send invite"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Verification status dialog */}
      <Dialog
        open={verifLink !== null}
        onOpenChange={(open) => {
          if (!open) setVerifLink(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>
              Employer verification —{" "}
              {verifLink?.candidate.full_name || verifLink?.candidate.email}
            </DialogTitle>
            <DialogDescription>
              Up to 3 previous employers. Overrides require a reason and are
              audit-logged.
            </DialogDescription>
          </DialogHeader>
          {!verifLink?.profile_id ? (
            <p className="text-sm text-muted-foreground">
              No profile / verification data for this candidate yet.
            </p>
          ) : verifRequests.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No verification requests found.
            </p>
          ) : (
            <div className="space-y-3">
              {verifRequests.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <div>
                    <p className="text-sm font-medium">{v.employer_email}</p>
                    <StatusBadge
                      status={v.overridden ? "overridden" : v.status}
                    />
                  </div>
                  {canGrantAccess &&
                  !v.overridden &&
                  v.status?.toLowerCase() !== "completed" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setOverrideTarget(v)}
                    >
                      Override
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Override dialog — reason required */}
      <Dialog
        open={overrideTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setOverrideTarget(null);
            setOverrideReason("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Override verification</DialogTitle>
            <DialogDescription>
              A reason is required. The override is written to the audit log.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Reason for overriding this verification requirement"
            rows={3}
            value={overrideReason}
            onChange={(e) => setOverrideReason(e.target.value)}
          />
          <DialogFooter>
            <Button
              disabled={busy || overrideReason.trim().length === 0}
              onClick={() => void submitOverride()}
            >
              {busy ? "Submitting…" : "Confirm override"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

"use client";

// Recruiter job workspace: resume upload (multipart, FR-4.3), matching
// results, interview scheduling (FR-8.3) and mandatory pipeline status
// updates (FR-8.4).

import * as React from "react";
import { useParams } from "next/navigation";
import { CalendarPlus, Upload } from "lucide-react";

import { apiGet, apiPost, apiUpload } from "@/lib/api";
import type { CandidateLink, Job } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { MatchingResults } from "@/components/matching-results";
import { StatusBadge } from "@/components/status-badge";
import { TierBadge } from "@/components/tier-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

export default function RecruiterJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const { toast } = useToast();

  const [job, setJob] = React.useState<Job | null>(null);
  const [links, setLinks] = React.useState<CandidateLink[]>([]);

  // Resume upload
  const [file, setFile] = React.useState<File | null>(null);
  const [uploadForm, setUploadForm] = React.useState({
    email: "",
    full_name: "",
    phone: "",
  });
  const [uploading, setUploading] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  // Interview scheduling
  const [interviewLink, setInterviewLink] = React.useState<CandidateLink | null>(null);
  const [interviewForm, setInterviewForm] = React.useState({
    scheduled_at: "",
    notes: "",
  });
  const [scheduling, setScheduling] = React.useState(false);

  React.useEffect(() => {
    apiGet<Job | { job: Job }>(`/jobs/${jobId}`)
      .then((res) => {
        const j =
          "job" in (res as object) && (res as { job?: Job }).job
            ? (res as { job: Job }).job
            : (res as Job);
        setJob(j);
      })
      .catch(() => {});
  }, [jobId]);

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
    void loadLinks();
  }, [loadLinks]);

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !uploadForm.email) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("email", uploadForm.email);
      if (uploadForm.full_name) fd.append("full_name", uploadForm.full_name);
      if (uploadForm.phone) fd.append("phone", uploadForm.phone);
      await apiUpload(`/candidates/jobs/${jobId}/upload-resume`, fd);
      toast({
        title: "Resume uploaded",
        description:
          "Candidate linked as freshly sourced; parsing has been queued.",
      });
      setFile(null);
      setUploadForm({ email: "", full_name: "", phone: "" });
      if (fileInputRef.current) fileInputRef.current.value = "";
      void loadLinks();
    } catch (err) {
      toast({
        title: "Upload failed",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setUploading(false);
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

      <Tabs defaultValue="sourcing">
        <TabsList>
          <TabsTrigger value="sourcing">Sourcing</TabsTrigger>
          <TabsTrigger value="matching">Matching</TabsTrigger>
          <TabsTrigger value="pipeline">Pipeline & Interviews</TabsTrigger>
        </TabsList>

        <TabsContent value="sourcing">
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
                  <Input
                    id="resume-file"
                    type="file"
                    accept=".pdf,.doc,.docx"
                    ref={fileInputRef}
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    required
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
        </TabsContent>

        <TabsContent value="matching">
          <MatchingResults jobId={jobId} />
        </TabsContent>

        <TabsContent value="pipeline">
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
                <TableHead>Pipeline update</TableHead>
                <TableHead className="text-right">Interview</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {links.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
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
                      <Select onValueChange={(v) => void updateStatus(link, v)}>
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
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1"
                        onClick={() => setInterviewLink(link)}
                      >
                        <CalendarPlus className="h-4 w-4" /> Schedule
                      </Button>
                    </TableCell>
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
    </div>
  );
}

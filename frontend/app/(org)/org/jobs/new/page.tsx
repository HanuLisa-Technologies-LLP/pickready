"use client";

// Staff job creation. Every staff role can create a job (capability
// `create_job`, granted equally to the flat staff roles).
//
// Rebuilt to the client's 2026-07-28 spec. What changed and why:
//   * ONE job description. The seven text boxes (description, role,
//     responsibilities, accountabilities, education, skills, experience) are
//     gone. The AI drafts a single formatted document, the recruitment team
//     edits it behind an explicit Edit button, and the structured fields the
//     API still stores are derived from that document.
//   * The sequence is now draft, then edit, then publish. Publish is disabled
//     until a description exists, because the client asked for the publish
//     action to appear only once there is something to publish.
//   * "Level" became an experience band, a minimum and a maximum in years.
//   * "Reporting to" is a dropdown of roles from the server, with an Others
//     option that reveals a free-text field.
//   * "Reportees" and "Company context" were removed outright.
//   * Publishing shows the public application link in a POPUP, ready to copy
//     into a LinkedIn or Naukri posting. A candidate opening that link is taken
//     to the candidate portal to sign in and apply for this job.

import * as React from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, ExternalLink, Sparkles } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import { buildJobCreatePayload, type JobFormValues, skillsToArray } from "@/lib/job-payload";
import { apiErrorMessage } from "@/lib/validation-errors";
import { JOB_GRADES, type JobGrade } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { JdEditor } from "@/components/jd-document";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField, FormSection } from "@/components/ui/form";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
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

/** The sentinel the server uses for "not in the list, type it yourself". */
const OTHERS = "Others";

interface ReportingToOptions {
  options: string[];
  other_value: string;
}

interface GeneratedJd {
  jd_markdown?: string | null;
}

interface CreatedJob {
  id?: string;
  public_application_url?: string | null;
}

/** Loosely unwrap a possibly-wrapped API payload. */
function pick<T = Record<string, unknown>>(res: unknown, key: string): T {
  if (res && typeof res === "object" && key in (res as object)) {
    const inner = (res as Record<string, unknown>)[key];
    if (inner && typeof inner === "object") return inner as T;
  }
  return (res ?? {}) as T;
}

export default function CreateJobPage() {
  const { toast } = useToast();
  const router = useRouter();

  const [busy, setBusy] = React.useState(false);
  const [generating, setGenerating] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [publishedLink, setPublishedLink] = React.useState<string | null>(null);

  const [form, setForm] = React.useState<JobFormValues>({
    title: "",
    department: "",
    grade: "",
    requirement_period: "",
    reporting_to: "",
    experience_min_years: "",
    experience_max_years: "",
    skills: "",
    jd_markdown: "",
  });

  // Free-form brief that seeds the draft. Not sent on create.
  const [brief, setBrief] = React.useState("");

  const [reportingOptions, setReportingOptions] = React.useState<string[]>([]);
  const [reportingChoice, setReportingChoice] = React.useState("");
  const [gradeError, setGradeError] = React.useState<string | null>(null);
  const [experienceError, setExperienceError] = React.useState<string | null>(null);
  // True right after a draft lands, so the editor opens ready to edit.
  const [justDrafted, setJustDrafted] = React.useState(false);

  const set = (key: keyof JobFormValues) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  React.useEffect(() => {
    let cancelled = false;
    apiGet<ReportingToOptions>("/jobs/reporting-to-options")
      .then((res) => {
        if (!cancelled) setReportingOptions(res.options ?? []);
      })
      .catch(() => {
        // A dropdown we could not load must not block job creation: fall back
        // to the free-text field by pre-selecting Others.
        if (!cancelled) {
          setReportingOptions([]);
          setReportingChoice(OTHERS);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** min must not exceed max. The database enforces this too. */
  const validateExperience = (): boolean => {
    const min = Number(form.experience_min_years);
    const max = Number(form.experience_max_years);
    if (!form.experience_min_years.trim() || !form.experience_max_years.trim()) {
      setExperienceError("Give both a minimum and a maximum.");
      return false;
    }
    if (Number.isFinite(min) && Number.isFinite(max) && min > max) {
      setExperienceError("The minimum cannot be more than the maximum.");
      return false;
    }
    setExperienceError(null);
    return true;
  };

  const generate = async () => {
    if (!form.title.trim()) {
      toast({
        title: "Add a job title first",
        description: "The AI needs at least a title to draft the description.",
        variant: "destructive",
      });
      return;
    }
    setGenerating(true);
    try {
      const res = await apiPost<unknown>("/jobs/generate-jd", {
        title: form.title,
        department: form.department || null,
        grade: form.grade || null,
        skills: skillsToArray(form.skills),
        key_requirements: brief,
        reporting_to: form.reporting_to || null,
        experience_min_years: Number(form.experience_min_years) || null,
        experience_max_years: Number(form.experience_max_years) || null,
      });
      const jd = pick<GeneratedJd>(res, "jd");
      const markdown =
        (res as GeneratedJd)?.jd_markdown ?? jd.jd_markdown ?? "";
      if (!markdown.trim()) throw new Error("The draft came back empty.");
      setForm((prev) => ({ ...prev, jd_markdown: markdown }));
      setJustDrafted(true);
      toast({
        title: "Draft ready",
        description: "Edit it to fit the role, then publish.",
      });
    } catch (error) {
      // The LLM router can 503 when every key is unhealthy. Never block the
      // recruiter: they can still write the description themselves.
      toast({
        title: "AI drafting is unavailable right now",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  };

  const publish = async () => {
    // Radix Select is not a native control, so `required` cannot gate it.
    if (!form.grade) {
      setGradeError("Select a grade. It decides which assessment the candidate receives.");
      document.getElementById("grade")?.scrollIntoView({ block: "center" });
      return;
    }
    setGradeError(null);
    if (!validateExperience()) {
      document.getElementById("experience_min_years")?.scrollIntoView({ block: "center" });
      return;
    }
    setBusy(true);
    try {
      const res = await apiPost<unknown>("/jobs", buildJobCreatePayload(form, true));
      const job = pick<CreatedJob>(res, "job");
      const link =
        (res as CreatedJob)?.public_application_url ??
        job.public_application_url ??
        (job.id && typeof window !== "undefined"
          ? `${window.location.origin}/apply/${job.id}`
          : "");
      setPublishedLink(link);
    } catch (err) {
      toast({
        title: "Could not publish the job",
        description: apiErrorMessage(err),
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const copyLink = async () => {
    if (!publishedLink) return;
    try {
      await navigator.clipboard.writeText(publishedLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({
        title: "Copy failed",
        description: "Select the link and copy it manually.",
        variant: "destructive",
      });
    }
  };

  const hasJd = form.jd_markdown.trim().length > 0;
  const usingOther = reportingChoice === OTHERS;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        eyebrow="Customer Portal"
        title="Create a job"
        description="Draft the description, edit it, then publish to get the public application link."
      />

      <Card>
        <CardContent className="space-y-8 pt-6">
          <FormSection title="Position">
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField label="Job title" htmlFor="title" required>
                <Input id="title" value={form.title} onChange={set("title")} required />
              </FormField>
              <FormField label="Department" htmlFor="department" required>
                <Input
                  id="department"
                  value={form.department}
                  onChange={set("department")}
                  required
                />
              </FormField>
              <FormField
                label="Grade"
                htmlFor="grade"
                required
                error={gradeError}
                hint="Decides the assessment a candidate receives for this role."
              >
                <Select
                  value={form.grade}
                  onValueChange={(value) => {
                    setForm((prev) => ({ ...prev, grade: value as JobGrade }));
                    setGradeError(null);
                  }}
                >
                  <SelectTrigger id="grade">
                    <SelectValue placeholder="Select a grade" />
                  </SelectTrigger>
                  <SelectContent>
                    {JOB_GRADES.map((grade) => (
                      <SelectItem key={grade.value} value={grade.value}>
                        {grade.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormField>
              <FormField
                label="Requirement period"
                htmlFor="requirement_period"
                required
                hint="How long this requirement stays open, e.g. Q3 2026."
              >
                <Input
                  id="requirement_period"
                  value={form.requirement_period}
                  onChange={set("requirement_period")}
                  required
                />
              </FormField>
            </div>

            {/* Experience band, replacing the old free-text Level. */}
            <FormField
              label="Experience"
              htmlFor="experience_min_years"
              required
              error={experienceError}
              hint="The range of experience this role expects, in years."
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex items-center gap-2">
                  <label htmlFor="experience_min_years" className="w-10 text-sm">
                    Min
                  </label>
                  <Input
                    id="experience_min_years"
                    type="number"
                    min={0}
                    max={60}
                    value={form.experience_min_years}
                    onChange={(e) => {
                      setExperienceError(null);
                      set("experience_min_years")(e);
                    }}
                    required
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label htmlFor="experience_max_years" className="w-10 text-sm">
                    Max
                  </label>
                  <Input
                    id="experience_max_years"
                    type="number"
                    min={0}
                    max={60}
                    value={form.experience_max_years}
                    onChange={(e) => {
                      setExperienceError(null);
                      set("experience_max_years")(e);
                    }}
                    required
                  />
                </div>
              </div>
            </FormField>

            <FormField
              label="Reporting to"
              htmlFor="reporting_to_choice"
              required
              hint="Pick the role this person reports to, or choose Others to type it."
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <Select
                  value={reportingChoice}
                  onValueChange={(value) => {
                    setReportingChoice(value);
                    setForm((prev) => ({
                      ...prev,
                      reporting_to: value === OTHERS ? "" : value,
                    }));
                  }}
                >
                  <SelectTrigger id="reporting_to_choice">
                    <SelectValue placeholder="Select a role" />
                  </SelectTrigger>
                  <SelectContent>
                    {reportingOptions.map((option) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                    {reportingOptions.includes(OTHERS) ? null : (
                      <SelectItem value={OTHERS}>{OTHERS}</SelectItem>
                    )}
                  </SelectContent>
                </Select>
                {usingOther ? (
                  <Input
                    id="reporting_to"
                    aria-label="Reporting to"
                    placeholder="Type the role"
                    value={form.reporting_to}
                    onChange={set("reporting_to")}
                    required
                  />
                ) : null}
              </div>
            </FormField>
          </FormSection>

          <Separator />

          <FormSection
            title="Draft with AI"
            description="Give the skills and a short brief. The AI writes the description, then you edit it."
          >
            <FormField
              label="Skills"
              htmlFor="skills"
              required
              hint="Comma-separated, e.g. Python, FastAPI, Postgres"
            >
              <Input id="skills" value={form.skills} onChange={set("skills")} required />
            </FormField>
            <FormField
              label="Brief"
              htmlFor="ai-brief"
              hint="Optional. Anything the AI should know: the team, the product, must-haves."
            >
              <Textarea
                id="ai-brief"
                rows={3}
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
              />
            </FormField>
            <Button
              type="button"
              variant="secondary"
              className="gap-2"
              onClick={() => void generate()}
              disabled={generating}
            >
              <Sparkles className="h-4 w-4" />
              {generating ? "Drafting" : hasJd ? "Draft again" : "Generate with AI"}
            </Button>
          </FormSection>

          <Separator />

          {/* The one JD document. Explicit Edit button, per the client. */}
          <JdEditor
            key={justDrafted ? "drafted" : "empty"}
            markdown={form.jd_markdown}
            initiallyEditing={justDrafted}
            onSave={(next) => {
              setForm((prev) => ({ ...prev, jd_markdown: next }));
              setJustDrafted(false);
              toast({ title: "Job description saved" });
            }}
          />

          {/* Publish appears only once there is a description to publish. */}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              disabled={busy || !hasJd}
              onClick={() => void publish()}
              className="w-full sm:w-auto"
            >
              {busy ? "Publishing" : "Publish job"}
            </Button>
            {hasJd ? null : (
              <p className="text-sm">
                Add a job description first. Publishing needs one.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* The shareable link, as a popup so it is impossible to miss. */}
      <Dialog
        open={publishedLink !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPublishedLink(null);
            router.push("/org/jobs");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Job published</DialogTitle>
            <DialogDescription>
              Share this link on LinkedIn, Naukri or anywhere else. Candidates
              who open it are taken to the candidate portal to sign in and apply
              for this role.
            </DialogDescription>
          </DialogHeader>

          {publishedLink ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input readOnly value={publishedLink} className="font-mono text-sm" />
              <Button type="button" onClick={() => void copyLink()} className="gap-2">
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? "Copied" : "Copy link"}
              </Button>
            </div>
          ) : (
            <p className="text-sm">
              The job was published. Open it from the Jobs list to find its
              application link.
            </p>
          )}

          <DialogFooter>
            {publishedLink ? (
              <Button asChild variant="outline" className="gap-1.5">
                <a href={publishedLink} target="_blank" rel="noreferrer">
                  <ExternalLink className="h-4 w-4" />
                  Preview
                </a>
              </Button>
            ) : null}
            <Button
              onClick={() => {
                setPublishedLink(null);
                router.push("/org/jobs");
              }}
            >
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

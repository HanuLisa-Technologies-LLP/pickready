"use client";

// Staff job creation (PRD v1.0). Every staff role can create a job (capability
// `create_job`, granted equally to the flat staff roles). Two authoring paths:
//   Path A — "Generate with AI": POST /jobs/generate-jd with a short brief; the
//            structured JD fields are filled in and remain fully editable.
//   Path B — manual entry of every JD field.
// On submit the job is created and published (flat model — no approval chain),
// and the public application link (origin + /apply/{job_uuid}) is shown to copy.

import * as React from "react";
import Link from "next/link";
import { Check, Copy, Sparkles } from "lucide-react";

import { apiPost } from "@/lib/api";
import type { JobJD } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField, FormSection } from "@/components/ui/form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

/** Loosely unwrap the created job / generated JD payload (shape may be wrapped). */
function pick<T = Record<string, unknown>>(res: unknown, key: string): T {
  if (res && typeof res === "object" && key in (res as object)) {
    const inner = (res as Record<string, unknown>)[key];
    if (inner && typeof inner === "object") return inner as T;
  }
  return (res ?? {}) as T;
}

export default function CreateJobPage() {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [generating, setGenerating] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [publishedLink, setPublishedLink] = React.useState<string | null>(null);
  const [publishedTitle, setPublishedTitle] = React.useState("");

  const [form, setForm] = React.useState({
    title: "",
    department: "",
    level: "",
    requirement_period: "",
    reporting_to: "",
    reportees: "",
    role: "",
    responsibilities: "",
    accountabilities: "",
    education: "",
    skills: "",
    experience_years: "",
  });

  // AI brief — only used to seed generation; not sent on the final create.
  const [brief, setBrief] = React.useState({
    requirements: "",
    company_context: "",
  });

  const set = (key: keyof typeof form) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => setForm({ ...form, [key]: e.target.value });

  const skillsToArray = (s: string) =>
    s.split(",").map((x) => x.trim()).filter(Boolean);

  const generate = async () => {
    if (!form.title.trim()) {
      toast({
        title: "Add a job title first",
        description: "The AI needs at least a title to draft the JD.",
        variant: "destructive",
      });
      return;
    }
    setGenerating(true);
    try {
      const res = await apiPost<unknown>("/jobs/generate-jd", {
        title: form.title,
        requirements: brief.requirements,
        skills: skillsToArray(form.skills),
        experience: form.experience_years,
        company_context: brief.company_context,
      });
      const jd = pick<Partial<JobJD>>(res, "jd");
      setForm((prev) => ({
        ...prev,
        reporting_to: jd.reporting_to ?? prev.reporting_to,
        reportees: jd.reportees ?? prev.reportees,
        role: jd.role ?? prev.role,
        responsibilities: jd.responsibilities ?? prev.responsibilities,
        accountabilities: jd.accountabilities ?? prev.accountabilities,
        education: jd.education ?? prev.education,
        skills:
          Array.isArray(jd.skills) && jd.skills.length > 0
            ? jd.skills.join(", ")
            : prev.skills,
        experience_years:
          typeof jd.experience_years === "number"
            ? String(jd.experience_years)
            : prev.experience_years,
      }));
      toast({
        title: "Draft generated",
        description: "Review and edit every field before publishing.",
      });
    } catch {
      // Defensive: the LLM router can 503 (all keys unhealthy) — never block
      // the recruiter; fall back to manual authoring.
      toast({
        title: "AI drafting is unavailable right now",
        description:
          "Please fill the JD fields manually — you can try AI again later.",
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await apiPost<unknown>("/jobs", {
        title: form.title,
        department: form.department,
        level: form.level,
        requirement_period: form.requirement_period,
        jd: {
          reporting_to: form.reporting_to,
          reportees: form.reportees,
          role: form.role,
          responsibilities: form.responsibilities,
          accountabilities: form.accountabilities,
          education: form.education,
          skills: skillsToArray(form.skills),
          experience_years: Number(form.experience_years) || 0,
        },
      });
      const job = pick<{ id?: string }>(res, "job");
      const jobId = typeof job.id === "string" ? job.id : undefined;
      if (jobId && typeof window !== "undefined") {
        setPublishedLink(`${window.location.origin}/apply/${jobId}`);
        setPublishedTitle(form.title);
      } else {
        // Created but no id returned — still confirm success.
        setPublishedLink("");
        setPublishedTitle(form.title);
      }
    } catch (err) {
      toast({
        title: "Could not create the job",
        description: err instanceof Error ? err.message : undefined,
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

  // Success state — job published, share the public application link.
  if (publishedLink !== null) {
    return (
      <div className="mx-auto max-w-2xl">
        <PageHeader
          title="Job published"
          description={`${publishedTitle} is live. Share the public application link with candidates.`}
        />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Public application link</CardTitle>
            <CardDescription>
              Anyone with this link can register and apply — no outreach email
              required.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {publishedLink ? (
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input readOnly value={publishedLink} className="font-mono text-sm" />
                <Button type="button" onClick={() => void copyLink()} className="gap-2">
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                  {copied ? "Copied" : "Copy link"}
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                The job was created. Open it from the Jobs list to find its
                application link.
              </p>
            )}
            <div className="flex gap-2">
              <Button asChild variant="outline">
                <Link href="/org/jobs">Back to jobs</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Create Job"
        description="Draft the JD with AI or enter it manually. Publishing makes the role's public application link available immediately."
      />
      <form onSubmit={submit}>
        <Card>
          <CardContent className="space-y-8 pt-6">
            <FormSection title="Position">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Job title" htmlFor="title" required>
                  <Input id="title" value={form.title} onChange={set("title")} required />
                </FormField>
                <FormField label="Department" htmlFor="department" required>
                  <Input id="department" value={form.department} onChange={set("department")} required />
                </FormField>
                <FormField label="Level" htmlFor="level" required>
                  <Input id="level" placeholder="e.g. L5 / Senior" value={form.level} onChange={set("level")} required />
                </FormField>
                <FormField
                  label="Requirement period"
                  htmlFor="requirement_period"
                  required
                  hint="How long this requirement stays open, e.g. Q3 2026."
                >
                  <Input id="requirement_period" value={form.requirement_period} onChange={set("requirement_period")} required />
                </FormField>
              </div>
            </FormSection>

            <Separator />

            {/* Path A — AI JD generation (FR-3.3). */}
            <FormSection
              title="Generate with AI"
              description="Give a short brief; the AI drafts the JD below. Everything stays editable."
            >
              <FormField
                label="Requirements brief"
                htmlFor="ai-requirements"
                hint="What the role needs — responsibilities, must-have skills, seniority."
              >
                <Textarea
                  id="ai-requirements"
                  rows={3}
                  value={brief.requirements}
                  onChange={(e) => setBrief({ ...brief, requirements: e.target.value })}
                />
              </FormField>
              <FormField
                label="Company context"
                htmlFor="ai-context"
                hint="Optional — team, product, or culture notes to ground the draft."
              >
                <Textarea
                  id="ai-context"
                  rows={2}
                  value={brief.company_context}
                  onChange={(e) => setBrief({ ...brief, company_context: e.target.value })}
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
                {generating ? "Generating…" : "Generate with AI"}
              </Button>
            </FormSection>

            <Separator />

            <FormSection title="Reporting structure">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Reporting to" htmlFor="reporting_to" required>
                  <Input id="reporting_to" value={form.reporting_to} onChange={set("reporting_to")} required />
                </FormField>
                <FormField label="Reportees" htmlFor="reportees">
                  <Input id="reportees" placeholder="e.g. 4 engineers" value={form.reportees} onChange={set("reportees")} />
                </FormField>
              </div>
            </FormSection>

            <Separator />

            <FormSection title="Role definition">
              <FormField label="Role" htmlFor="role" required>
                <Textarea id="role" rows={3} value={form.role} onChange={set("role")} required />
              </FormField>
              <FormField label="Responsibilities" htmlFor="responsibilities" required>
                <Textarea id="responsibilities" rows={4} value={form.responsibilities} onChange={set("responsibilities")} required />
              </FormField>
              <FormField label="Accountabilities" htmlFor="accountabilities" required>
                <Textarea id="accountabilities" rows={4} value={form.accountabilities} onChange={set("accountabilities")} required />
              </FormField>
            </FormSection>

            <Separator />

            <FormSection title="Requirements">
              <FormField label="Education" htmlFor="education" required>
                <Input id="education" value={form.education} onChange={set("education")} required />
              </FormField>
              <FormField
                label="Skills"
                htmlFor="skills"
                required
                hint="Comma-separated, e.g. Python, FastAPI, Postgres"
              >
                <Input id="skills" value={form.skills} onChange={set("skills")} required />
              </FormField>
              <FormField label="Experience (years)" htmlFor="experience_years" required>
                <Input
                  id="experience_years"
                  type="number"
                  min={0}
                  value={form.experience_years}
                  onChange={set("experience_years")}
                  required
                />
              </FormField>
            </FormSection>

            <Button type="submit" disabled={busy} className="w-full sm:w-auto">
              {busy ? "Publishing…" : "Create & publish job"}
            </Button>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}

"use client";

// Full JD creation form per FR-3.1: Title, Department, Level, Reporting To,
// Reportees, Role, Responsibilities, Accountabilities, Education, Skills,
// Experience (years), Requirement period.

import * as React from "react";
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField, FormSection } from "@/components/ui/form";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

export default function CreateJobPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
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

  const set = (key: keyof typeof form) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => setForm({ ...form, [key]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await apiPost("/jobs", {
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
          skills: form.skills
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          experience_years: Number(form.experience_years) || 0,
        },
      });
      toast({
        title: "JD created",
        description: "Saved as draft — submit it to start the approval chain.",
      });
      router.push("/org/jobs");
    } catch (err) {
      toast({
        title: "Could not create JD",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Create Job Description"
        description="All fields per the standard JD structure. The job starts as a draft."
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
              {busy ? "Creating…" : "Create JD"}
            </Button>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}

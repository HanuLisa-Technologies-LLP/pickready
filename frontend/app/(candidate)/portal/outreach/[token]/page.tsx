"use client";

// Outreach completion (FR-5.1/5.2, FR-6.1): personal fields, the 40-aspect
// questionnaire (minus aspects already covered), up to 3 previous-employer
// HR emails, and a fresh resume upload — submitted as multipart to the
// public tokenized endpoint.

import * as React from "react";
import { useParams } from "next/navigation";
import { CheckCircle2 } from "lucide-react";

import { apiGet, apiUploadWithProgress } from "@/lib/api";
import type { OutreachRequestInfo } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { AspectsForm, type AspectAnswers } from "@/components/aspects-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField, FormSection } from "@/components/ui/form";
import { ResumeFileInput } from "@/components/resume-file-input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

// Personal fields (FR-5.1) already cover aspects 1 (full name) and 2 (city).
const COVERED_ASPECT_IDS = [1, 2];

export default function OutreachCompletionPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const { toast } = useToast();

  const [info, setInfo] = React.useState<OutreachRequestInfo | null>(null);
  const [notFound, setNotFound] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const [personal, setPersonal] = React.useState({
    full_name: "",
    residing_city: "",
    age: "",
    gender: "",
  });
  const [answers, setAnswers] = React.useState<AspectAnswers>({});
  const [employerEmails, setEmployerEmails] = React.useState(["", "", ""]);
  const [resume, setResume] = React.useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [resumeError, setResumeError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<OutreachRequestInfo>(`/portal/outreach/${token}`)
      .then(setInfo)
      .catch(() => setNotFound(true));
  }, [token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resume) {
      toast({
        title: "Resume required",
        description: "Please attach an updated resume.",
        variant: "destructive",
      });
      return;
    }
    setBusy(true);
    setUploadProgress(0);
    setResumeError(null);
    try {
      const fd = new FormData();
      fd.append("full_name", personal.full_name);
      fd.append("residing_city", personal.residing_city);
      fd.append("age", personal.age);
      fd.append("gender", personal.gender);
      fd.append(
        "aspects",
        JSON.stringify(
          answers
        )
      );
      for (const email of employerEmails.map((s) => s.trim()).filter(Boolean)) {
        fd.append("employer_emails", email);
      }
      fd.append("resume", resume);
      await apiUploadWithProgress(`/portal/outreach/${token}`, fd, setUploadProgress);
      setSubmitted(true);
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : "Submission failed. Please retry.");
      toast({
        title: "Submission failed",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  if (notFound) {
    return (
      <CenteredCard
        title="Link not valid"
        description="This outreach link has expired or was already used. Please contact the HR team that reached out to you."
      />
    );
  }

  if (submitted) {
    return (
      <CenteredCard
        title="Thank you!"
        description="Your details, questionnaire and resume were submitted. The HR team will verify your previous employment and get back to you."
        icon={<CheckCircle2 className="h-10 w-10" />}
      />
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">
            Complete your candidate profile
            {info?.job_title ? ` — ${info.job_title}` : ""}
          </CardTitle>
          <CardDescription>
            {info?.tenant_name
              ? `Requested by ${info.tenant_name} via PickReady. `
              : ""}
            Please fill everything below — all sections are required before your
            profile can move forward.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-10" onSubmit={submit}>
            <FormSection
              title="Personal details"
              description="As per PF records / Class X memorandum."
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Full name" htmlFor="p-name" required>
                  <Input
                    id="p-name"
                    value={personal.full_name}
                    onChange={(e) =>
                      setPersonal({ ...personal, full_name: e.target.value })
                    }
                    required
                  />
                </FormField>
                <FormField label="Residing city" htmlFor="p-city" required>
                  <Input
                    id="p-city"
                    value={personal.residing_city}
                    onChange={(e) =>
                      setPersonal({
                        ...personal,
                        residing_city: e.target.value,
                      })
                    }
                    required
                  />
                </FormField>
                <FormField label="Age" htmlFor="p-age" required>
                  <Input
                    id="p-age"
                    type="number"
                    min={16}
                    max={100}
                    value={personal.age}
                    onChange={(e) =>
                      setPersonal({ ...personal, age: e.target.value })
                    }
                    required
                  />
                </FormField>
                <FormField label="Gender" required>
                  <Select
                    value={personal.gender}
                    onValueChange={(v) =>
                      setPersonal({ ...personal, gender: v })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="female">Female</SelectItem>
                      <SelectItem value="male">Male</SelectItem>
                      <SelectItem value="other">Other</SelectItem>
                      <SelectItem value="prefer_not_to_say">
                        Prefer not to say
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
              </div>
            </FormSection>

            <Separator />

            <FormSection
              title="Updated resume"
              description="A fresh upload is required — previous resumes are not reused."
            >
              <FormField label="Resume file" htmlFor="p-resume" required>
                <ResumeFileInput
                  id="p-resume"
                  file={resume}
                  progress={uploadProgress}
                  error={resumeError}
                  disabled={busy}
                  onFileChange={(file, error) => {
                    setResume(file);
                    setResumeError(error);
                    setUploadProgress(0);
                  }}
                  onRetry={() => void (document.querySelector("form") as HTMLFormElement | null)?.requestSubmit()}
                />
              </FormField>
            </FormSection>

            <Separator />

            <FormSection
              title="Questionnaire"
              description="The 40-aspect questionnaire. Aspect 40 is your consent to be matched against future roles."
            >
              <AspectsForm
                answers={answers}
                onChange={setAnswers}
                excludeIds={COVERED_ASPECT_IDS}
              />
            </FormSection>

            <Separator />

            <FormSection
              title="Previous employer HR contacts"
              description="Official HR email IDs of up to 3 previous employers (NOT your current employer). These are used for employment verification."
            >
              <div className="space-y-3">
                {employerEmails.map((email, i) => (
                  <FormField
                    key={i}
                    label={`Previous employer ${i + 1} HR email${i === 0 ? "" : " (optional)"}`}
                    htmlFor={`emp-${i}`}
                  >
                    <Input
                      id={`emp-${i}`}
                      type="email"
                      placeholder="hr@previous-company.com"
                      value={email}
                      onChange={(e) => {
                        const next = employerEmails.slice();
                        next[i] = e.target.value;
                        setEmployerEmails(next);
                      }}
                    />
                  </FormField>
                ))}
              </div>
            </FormSection>

            <Button type="submit" size="lg" disabled={busy} className="w-full">
              {busy ? "Submitting…" : "Submit profile"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function CenteredCard({
  title,
  description,
  icon,
}: {
  title: string;
  description: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md text-center">
        <CardHeader className="items-center">
          {icon ? <div className="mb-2 flex justify-center">{icon}</div> : null}
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

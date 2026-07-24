"use client";

// PUBLIC job-application page (PRD v1.0 — FR-3.4/3.5/9.1).
//
// Route choice: `/apply/[job_uuid]` (NOT a bare root `/[job_uuid]` catch-all).
// A bare dynamic segment at the app root would sit beside the existing static
// top-level routes (/login, /register, /org, /admin, /portal, /verify-…) and
// any typo or future route would silently resolve to "job not found" — too
// risky. `/apply/{uuid}` is unambiguous and collision-free.
//
// Flow: load the published job (public) → candidate register/sign-in (reuses
// Firebase auth, no redirect) → personal details + 40-question questionnaire →
// upload a fresh resume OR reuse the last one (FR-6.2) → submit → confirmation.
// This is NOT outreach-gated: anyone with the link can apply.

import * as React from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, Loader2 } from "lucide-react";

import { apiGet, apiUpload } from "@/lib/api";
import type { JobJD } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { ApplyAuth } from "@/components/apply-auth";
import { AspectsForm, type AspectAnswers } from "@/components/aspects-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { FormField, FormSection } from "@/components/ui/form";
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

// Personal fields below cover aspects 1 (full name) and 2 (residing city).
const COVERED_ASPECT_IDS = [1, 2];

/** Shape of a published job as returned by the public/portal job view. */
interface PublicJob {
  id: string;
  title: string;
  department?: string | null;
  level?: string | null;
  company_name?: string | null;
  tenant_name?: string | null;
  jd?: Partial<JobJD> | null;
}

function unwrapJob(res: unknown): PublicJob | null {
  if (!res || typeof res !== "object") return null;
  const obj = res as Record<string, unknown>;
  const job = (obj.job ?? obj) as Record<string, unknown>;
  if (typeof job.id !== "string" || typeof job.title !== "string") return null;
  return job as unknown as PublicJob;
}

export default function PublicApplyPage() {
  const params = useParams<{ job_uuid: string }>();
  const jobUuid = params.job_uuid;
  const { toast } = useToast();
  const { user, loading: authLoading, refresh } = useAuth();

  const isCandidate = user?.role === "candidate";

  const [job, setJob] = React.useState<PublicJob | null>(null);
  const [loadingJob, setLoadingJob] = React.useState(true);
  const [notFound, setNotFound] = React.useState(false);

  const [personal, setPersonal] = React.useState({
    full_name: "",
    residing_city: "",
    age: "",
    gender: "",
  });
  const [answers, setAnswers] = React.useState<AspectAnswers>({});
  const [resumeMode, setResumeMode] = React.useState<"upload" | "reuse">(
    "upload"
  );
  const [resume, setResume] = React.useState<File | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);

  // Prefill the applicant's name once the candidate session is known.
  React.useEffect(() => {
    if (user?.full_name) {
      setPersonal((p) => (p.full_name ? p : { ...p, full_name: user.full_name }));
    }
  }, [user?.full_name]);

  // Load the published job. Try the general job view first, then the portal
  // fallback (the exact public path is finalized by the job-service agents).
  const loadJob = React.useCallback(async () => {
    setLoadingJob(true);
    setNotFound(false);
    // Public read FIRST so an unauthenticated visitor sees the job before
    // signing in (FR-3.5). Auth-scoped paths are fallbacks (retried post-login).
    const paths = [
      `/jobs/public/${jobUuid}`,
      `/portal/jobs/${jobUuid}`,
      `/jobs/${jobUuid}`,
    ];
    for (const path of paths) {
      try {
        const res = await apiGet<unknown>(path);
        const parsed = unwrapJob(res);
        if (parsed) {
          setJob(parsed);
          setLoadingJob(false);
          return;
        }
      } catch {
        // A 401 while unauthenticated is expected on the auth-scoped path — keep
        // trying the fallbacks; the effect re-runs after sign-in.
      }
    }
    setLoadingJob(false);
    setNotFound(true);
  }, [jobUuid]);

  React.useEffect(() => {
    void loadJob();
  }, [loadJob]);

  // Once the candidate signs in, retry the job fetch if it wasn't public.
  React.useEffect(() => {
    if (isCandidate && !job) void loadJob();
  }, [isCandidate, job, loadJob]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (resumeMode === "upload" && !resume) {
      toast({
        title: "Resume required",
        description: "Attach a resume, or choose to reuse your last one.",
        variant: "destructive",
      });
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("full_name", personal.full_name);
      fd.append("residing_city", personal.residing_city);
      fd.append("age", personal.age);
      fd.append("gender", personal.gender);
      fd.append(
        "aspects",
        JSON.stringify(
          Object.entries(answers).map(([id, answer]) => ({
            aspect_id: Number(id),
            answer,
          }))
        )
      );
      if (resumeMode === "reuse") {
        fd.append("reuse_previous", "true");
      } else if (resume) {
        fd.append("resume", resume);
      }
      await apiUpload(`/portal/jobs/${jobUuid}/apply`, fd);
      setSubmitted(true);
    } catch (err) {
      toast({
        title: "Application failed",
        description:
          err instanceof Error
            ? err.message
            : "Something went wrong. Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  // ----- Render states -----

  if (loadingJob || authLoading) {
    return (
      <Centered>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Loading job…</p>
      </Centered>
    );
  }

  if (notFound || !job) {
    return (
      <Centered>
        <CardTitle>Job not available</CardTitle>
        <CardDescription>
          This job link is invalid, or the role is no longer open. Please check
          the link with the employer who shared it.
        </CardDescription>
      </Centered>
    );
  }

  if (submitted) {
    return (
      <Centered>
        <CheckCircle2 className="h-10 w-10 text-foreground" />
        <CardTitle>Application submitted</CardTitle>
        <CardDescription>
          Thanks for applying to {job.title}. You can track this application from
          your candidate portal under “My Applications”.
        </CardDescription>
        <Button asChild className="mt-2">
          <a href="/portal/applications">Go to my applications</a>
        </Button>
      </Centered>
    );
  }

  const companyName = job.company_name ?? job.tenant_name ?? undefined;
  const jd = job.jd ?? {};

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-3xl items-center px-4">
          <span className="text-lg font-bold tracking-tight">PickReady</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10">
        {/* Published job summary */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="text-2xl">{job.title}</CardTitle>
            <CardDescription>
              {[companyName, job.department, job.level]
                .filter(Boolean)
                .join(" · ") || "Open role"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            {jd.role ? (
              <div>
                <h3 className="mb-1 font-semibold">Role</h3>
                <p className="whitespace-pre-line text-muted-foreground">
                  {jd.role}
                </p>
              </div>
            ) : null}
            {jd.responsibilities ? (
              <div>
                <h3 className="mb-1 font-semibold">Responsibilities</h3>
                <p className="whitespace-pre-line text-muted-foreground">
                  {jd.responsibilities}
                </p>
              </div>
            ) : null}
            {jd.education ? (
              <div>
                <h3 className="mb-1 font-semibold">Education</h3>
                <p className="text-muted-foreground">{jd.education}</p>
              </div>
            ) : null}
            {jd.skills && jd.skills.length > 0 ? (
              <div>
                <h3 className="mb-1 font-semibold">Skills</h3>
                <div className="flex flex-wrap gap-1.5">
                  {jd.skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}
            {typeof jd.experience_years === "number" && jd.experience_years > 0 ? (
              <p className="text-muted-foreground">
                Experience: {jd.experience_years}+ years
              </p>
            ) : null}
          </CardContent>
        </Card>

        {/* Step: candidate auth (skipped if already a candidate) */}
        {!isCandidate ? (
          <Card>
            <CardHeader>
              <CardTitle>Apply for this role</CardTitle>
              <CardDescription>
                {user
                  ? "You're signed in with a non-candidate account. Sign in with a candidate account to apply."
                  : "Create a candidate account or sign in to continue your application."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ApplyAuth onAuthed={() => void refresh()} />
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>Your application</CardTitle>
              <CardDescription>
                All sections are required before you can submit.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-10" onSubmit={submit}>
                <FormSection
                  title="Personal details"
                  description="As per PF records / Class X memorandum."
                >
                  <div className="grid gap-4 sm:grid-cols-2">
                    <FormField label="Full name" htmlFor="ap-name" required>
                      <Input
                        id="ap-name"
                        value={personal.full_name}
                        onChange={(e) =>
                          setPersonal({ ...personal, full_name: e.target.value })
                        }
                        required
                      />
                    </FormField>
                    <FormField label="Residing city" htmlFor="ap-city" required>
                      <Input
                        id="ap-city"
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
                    <FormField label="Age" htmlFor="ap-age" required>
                      <Input
                        id="ap-age"
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
                  title="Resume"
                  description="Upload a fresh resume, or reuse the last one you submitted."
                >
                  <div
                    className="grid gap-2 sm:grid-cols-2"
                    role="radiogroup"
                    aria-label="Resume option"
                  >
                    <Button
                      type="button"
                      variant={resumeMode === "upload" ? "secondary" : "outline"}
                      aria-pressed={resumeMode === "upload"}
                      onClick={() => setResumeMode("upload")}
                    >
                      Upload a new resume
                    </Button>
                    <Button
                      type="button"
                      variant={resumeMode === "reuse" ? "secondary" : "outline"}
                      aria-pressed={resumeMode === "reuse"}
                      onClick={() => {
                        setResumeMode("reuse");
                        setResume(null);
                      }}
                    >
                      Reuse my last resume
                    </Button>
                  </div>
                  {resumeMode === "upload" ? (
                    <FormField
                      label="Resume file"
                      htmlFor="ap-resume"
                      required
                      hint="PDF, DOC or DOCX."
                    >
                      <Input
                        id="ap-resume"
                        type="file"
                        accept=".pdf,.doc,.docx"
                        onChange={(e) => setResume(e.target.files?.[0] ?? null)}
                      />
                    </FormField>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      We&apos;ll attach the most recent resume on your record. If
                      you don&apos;t have one yet, upload a new file instead.
                    </p>
                  )}
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

                <Button
                  type="submit"
                  size="lg"
                  className="w-full"
                  disabled={busy}
                >
                  {busy ? "Submitting…" : "Submit application"}
                </Button>
              </form>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4 text-foreground">
      <Card className="w-full max-w-md text-center">
        <CardHeader className="items-center gap-2">{children}</CardHeader>
      </Card>
    </div>
  );
}

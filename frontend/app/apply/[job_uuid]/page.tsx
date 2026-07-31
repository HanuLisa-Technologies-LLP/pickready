"use client";

// PUBLIC job-application page (PRD v1.0, FR-3.4/3.5/9.1).
//
// Route choice: `/apply/[job_uuid]` (NOT a bare root `/[job_uuid]` catch-all).
// A bare dynamic segment at the app root would sit beside the existing static
// top-level routes (/login, /register, /org, /admin, /portal, /verify-…) and
// any typo or future route would silently resolve to "job not found", too
// risky. `/apply/{uuid}` is unambiguous and collision-free.
//
// JOB DESCRIPTION FIRST: the full JD renders for an UNAUTHENTICATED visitor
// before anything is asked of them. The page is two tabs, "Job description"
// and "Apply", mounted side by side so the candidate can re-read the JD
// mid-application without losing a single answer (both panels stay mounted;
// state lives in this component, not in the tab panels).

import * as React from "react";
import { useParams } from "next/navigation";
import { AlertCircle, CheckCircle2, Clock, Loader2 } from "lucide-react";

import { ApiError, apiGet, apiUploadWithProgress } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { ApplyAuth } from "@/components/apply-auth";
import {
  ApplicationValidationForm,
  missingValidationFields,
  type ValidationFieldSpec,
  type ValidationValues,
} from "@/components/application-validation-form";
import {
  AspectsForm,
  aspectProgress,
  missingAspects,
  type AspectAnswers,
} from "@/components/aspects-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { FormField, FormSection } from "@/components/ui/form";
import {
  ResumeChoice,
  type ResumeMode,
  type StoredResume,
} from "@/components/resume-file-input";
import { Card, CardContent } from "@/components/ui/card";
import { PublicNotice, PublicShell } from "@/components/public-shell";
import { InlineError, Section } from "@/components/page-primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Personal fields below cover aspects 1 (full name) and 2 (residing city).
const COVERED_ASPECT_IDS = [1, 2];

/**
 * The public job payload. `GET /jobs/public/{id}` (schemas/jobs.PublicJobOut)
 * returns the JD under `jd_json`, NOT `jd`. Reading the wrong key is what
 * left applicants staring at a bare title, so both are accepted here and the
 * canonical `jd_json` wins.
 */
interface PublicJob {
  id: string;
  title: string;
  department?: string | null;
  level?: string | null;
  company_name?: string | null;
  tenant_name?: string | null;
  jd_json?: Record<string, unknown> | null;
  jd?: Record<string, unknown> | null;
  created_at?: string | null;
}

function unwrapJob(res: unknown): PublicJob | null {
  if (!res || typeof res !== "object") return null;
  const obj = res as Record<string, unknown>;
  const job = (obj.job ?? obj) as Record<string, unknown>;
  if (typeof job.id !== "string" || typeof job.title !== "string") return null;
  return job as unknown as PublicJob;
}

// ── JD rendering ────────────────────────────────────────────────────────────
// Live JD payloads mix shapes: `responsibilities` and `accountabilities` come
// back as string[] from the AI generator but as a paragraph when typed by
// hand, and `reportees` can be a number. Everything is normalised rather than
// assuming one shape and rendering "[object Object]".

function asLines(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((v) => String(v).trim()).filter(Boolean);
  }
  if (typeof value === "number") return [String(value)];
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return [];
    // A hand-typed block often arrives as newline- or bullet-separated text.
    const lines = trimmed
      .split(/\r?\n+/)
      .map((l) => l.replace(/^[-•*]\s*/, "").trim())
      .filter(Boolean);
    return lines.length > 1 ? lines : [trimmed];
  }
  return [];
}

function jdText(value: unknown): string | null {
  const lines = asLines(value);
  return lines.length ? lines.join(" ") : null;
}

/** "~3 min read" from the whole JD at 200 wpm; never reports "0 min". */
function readTimeMinutes(jd: Record<string, unknown>): number {
  const words = Object.values(jd)
    .flatMap((v) => asLines(v))
    .join(" ")
    .split(/\s+/)
    .filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}

function JdBlock({ title, value }: { title: string; value: unknown }) {
  const lines = asLines(value);
  if (lines.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-600">
        {title}
      </h3>
      {lines.length === 1 ? (
        <p className="whitespace-pre-line text-pretty text-sm leading-7">
          {lines[0]}
        </p>
      ) : (
        <ul className="list-disc space-y-1.5 pl-5 text-sm leading-7 marker:text-brand-600">
          {lines.map((line, i) => (
            <li key={`${title}-${i}`}>{line}</li>
          ))}
        </ul>
      )}
    </section>
  );
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
  const [tab, setTab] = React.useState<"jd" | "apply">("jd");
  const [candidateSessionVerified, setCandidateSessionVerified] =
    React.useState(false);
  const [checkingCandidateSession, setCheckingCandidateSession] =
    React.useState(false);
  const [authCheckVersion, setAuthCheckVersion] = React.useState(0);

  const [personal, setPersonal] = React.useState({
    full_name: "",
    residing_city: "",
    age: "",
    gender: "",
  });
  const [answers, setAnswers] = React.useState<AspectAnswers>({});
  const [resumeMode, setResumeMode] = React.useState<ResumeMode>("upload");
  const [resume, setResume] = React.useState<File | null>(null);
  const [storedResume, setStoredResume] = React.useState<StoredResume | null>(
    null
  );
  const [loadingContext, setLoadingContext] = React.useState(false);
  const [alreadyApplied, setAlreadyApplied] = React.useState(false);
  const [appliedAt, setAppliedAt] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [uploadProgress, setUploadProgress] = React.useState(0);
  const [resumeError, setResumeError] = React.useState<string | null>(null);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [missingIds, setMissingIds] = React.useState<number[]>([]);
  const [missingPersonal, setMissingPersonal] = React.useState<string[]>([]);
  const [submitted, setSubmitted] = React.useState(false);
  // The six mandatory fields (spec §7), defined server-side so the form and the
  // report's Validation section cannot drift apart.
  const [validationFields, setValidationFields] = React.useState<ValidationFieldSpec[]>([]);
  const [validation, setValidation] = React.useState<ValidationValues>({});
  const errorRef = React.useRef<HTMLDivElement | null>(null);

  // Prefill the applicant's name once the candidate session is known.
  React.useEffect(() => {
    if (user?.full_name) {
      setPersonal((p) => (p.full_name ? p : { ...p, full_name: user.full_name }));
    }
  }, [user?.full_name]);

  // Load the published job. The PUBLIC read comes first so an unauthenticated
  // visitor sees the whole JD before signing in (FR-3.5); the auth-scoped
  // paths are fallbacks retried after sign-in.
  const loadJob = React.useCallback(async () => {
    setLoadingJob(true);
    setNotFound(false);
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
        // 401 on an auth-scoped path while signed out is expected, keep going.
      }
    }
    setLoadingJob(false);
    setNotFound(true);
  }, [jobUuid]);

  React.useEffect(() => {
    void loadJob();
  }, [loadJob]);

  React.useEffect(() => {
    if (isCandidate && !job) void loadJob();
  }, [isCandidate, job, loadJob]);

  // A client-side identity is not enough to unlock an application. Confirm the
  // httpOnly backend session first, otherwise a stale UI state can expose the
  // questionnaire and only fail after the candidate has completed it.
  React.useEffect(() => {
    let active = true;
    if (!isCandidate) {
      setCandidateSessionVerified(false);
      setCheckingCandidateSession(false);
      return () => {
        active = false;
      };
    }
    setCheckingCandidateSession(true);
    void apiGet<{ user: { role: string } }>("/auth/me")
      .then((session) => {
        if (active) setCandidateSessionVerified(session.user.role === "candidate");
      })
      .catch(() => {
        if (active) setCandidateSessionVerified(false);
      })
      .finally(() => {
        if (active) setCheckingCandidateSession(false);
      });
    return () => {
      active = false;
    };
  }, [isCandidate, authCheckVersion]);

  // Once the session is real, find out whether they already applied and
  // whether a stored resume can be reused, BEFORE they fill 40 answers.
  React.useEffect(() => {
    if (!candidateSessionVerified) return;
    let active = true;
    setLoadingContext(true);
    void apiGet<{
      already_applied?: boolean;
      applied_at?: string | null;
      resume?: StoredResume;
      validation_fields?: ValidationFieldSpec[];
    }>(`/portal/jobs/${jobUuid}/apply-context`)
      .then((ctx) => {
        if (!active) return;
        setAlreadyApplied(Boolean(ctx.already_applied));
        setAppliedAt(ctx.applied_at ?? null);
        setValidationFields(ctx.validation_fields ?? []);
        const stored = ctx.resume ?? { has_resume: false };
        setStoredResume(stored);
        if (stored.has_resume) setResumeMode("reuse");
      })
      .catch(() => {
        if (active) setStoredResume({ has_resume: false });
      })
      .finally(() => {
        if (active) setLoadingContext(false);
      });
    return () => {
      active = false;
    };
  }, [candidateSessionVerified, jobUuid]);

  const validate = (): boolean => {
    const personalGaps: string[] = [];
    if (!personal.full_name.trim()) personalGaps.push("Full name");
    if (!personal.residing_city.trim()) personalGaps.push("Residing city");
    if (!personal.age.trim()) personalGaps.push("Age");
    if (!personal.gender) personalGaps.push("Gender");
    const gaps = missingAspects(answers, COVERED_ASPECT_IDS);
    setMissingPersonal(personalGaps);
    setMissingIds(gaps.map((a) => a.id));

    const resumeMissing =
      resumeMode === "upload" ? !resume : !storedResume?.has_resume;
    setResumeError(
      resumeMissing
        ? resumeMode === "upload"
          ? "Attach a PDF or DOCX resume (up to 10 MB)."
          : "There is no resume on your record yet. Upload one instead."
        : null
    );

    const validationGaps = missingValidationFields(validationFields, validation);

    if (
      personalGaps.length === 0 &&
      gaps.length === 0 &&
      validationGaps.length === 0 &&
      !resumeMissing
    ) {
      setSubmitError(null);
      return true;
    }
    const parts: string[] = [];
    if (personalGaps.length) parts.push(`${personalGaps.join(", ")}`);
    if (validationGaps.length) parts.push(validationGaps.join(", "));
    if (gaps.length) {
      const shown = gaps.slice(0, 6).map((a) => `Q${a.id}`).join(", ");
      parts.push(
        gaps.length > 6
          ? `${gaps.length} questionnaire answers (${shown} and more)`
          : `questionnaire ${shown}`
      );
    }
    if (resumeMissing) parts.push("a resume");
    setSubmitError(`Still needed: ${parts.join("; ")}.`);
    return false;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!candidateSessionVerified) {
      toast({
        title: "Sign in required",
        description:
          "Sign in with a candidate account before starting your application.",
        variant: "destructive",
      });
      return;
    }
    if (!validate()) {
      errorRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    setBusy(true);
    setUploadProgress(0);
    try {
      const fd = new FormData();
      fd.append("full_name", personal.full_name.trim());
      fd.append("residing_city", personal.residing_city.trim());
      if (personal.age.trim()) fd.append("age", personal.age.trim());
      if (personal.gender) fd.append("gender", personal.gender);
      fd.append("aspects", JSON.stringify(answers));
      fd.append("validation", JSON.stringify(validation));
      if (resumeMode === "reuse") {
        fd.append("reuse_previous", "true");
      } else if (resume) {
        fd.append("resume", resume);
      }
      await apiUploadWithProgress(
        `/portal/jobs/${jobUuid}/apply`,
        fd,
        setUploadProgress
      );
      setSubmitted(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAlreadyApplied(true);
        return;
      }
      const message =
        err instanceof Error
          ? err.message
          : "Something went wrong. Please try again.";
      setSubmitError(message);
      setResumeError(resumeMode === "upload" ? message : null);
      toast({
        title: "Application failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  // ----- Render states -----

  if (loadingJob || authLoading) {
    return (
      <PublicNotice
        icon={<Loader2 className="h-7 w-7 animate-spin" aria-hidden="true" />}
        title="Loading this role"
        description="One moment while we fetch the job description."
      />
    );
  }

  if (notFound || !job) {
    return (
      <PublicNotice
        tone="error"
        icon={<AlertCircle className="h-7 w-7" aria-hidden="true" />}
        title="This job is not available"
        description="The link may be mistyped, or the role has been closed. Check the link with the employer who shared it."
      />
    );
  }

  const companyName = job.company_name ?? job.tenant_name ?? undefined;
  const jd = (job.jd_json ?? job.jd ?? {}) as Record<string, unknown>;
  const subtitle =
    [companyName, job.department, job.level].filter(Boolean).join(" · ") ||
    "Open role";

  if (submitted) {
    return (
      <PublicNotice
        tone="success"
        icon={<CheckCircle2 className="h-7 w-7" aria-hidden="true" />}
        title="Application submitted"
        description={
          <>
            Thanks for applying to {job.title}
            {companyName ? ` at ${companyName}` : ""}. You can follow it under
            Applied Jobs in your candidate portal.
          </>
        }
        action={
          <Button asChild size="lg">
            <a href="/portal/applications">Go to Applied Jobs</a>
          </Button>
        }
      />
    );
  }

  const readMinutes = readTimeMinutes(jd);
  const hasJdContent = Object.values(jd).some((v) => asLines(v).length > 0);
  const progress = aspectProgress(answers, COVERED_ASPECT_IDS);

  return (
    <PublicShell>
        {/* The role, stated before anything is asked of the candidate. */}
        <div className="mb-8">
          <div className="flex items-start gap-4">
            <div
              className="grid h-14 w-14 shrink-0 place-items-center rounded-xl bg-brand-600 text-base font-bold text-white shadow-brand"
              aria-hidden="true"
            >
              {(companyName ?? "PR")
                .split(/\s+/)
                .slice(0, 2)
                .map((part) => part[0])
                .join("")
                .toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-600">
                {companyName ?? "Hiring company"}
              </p>
              <h1 className="mt-1.5 text-balance text-2xl font-bold tracking-tight sm:text-3xl">
                {job.title}
              </h1>
              <p className="mt-2 text-sm leading-6">{subtitle}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 font-medium">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              About {readMinutes} min read
            </span>
            {typeof jd.experience_years === "number" && jd.experience_years > 0 ? (
              <span className="inline-flex items-center rounded-full border border-border bg-surface px-3 py-1 font-medium">
                {jd.experience_years}+ years experience
              </span>
            ) : null}
          </div>
        </div>

        <Tabs value={tab} onValueChange={(v) => setTab(v as "jd" | "apply")}>
          <TabsList className="w-full sm:w-auto">
            <TabsTrigger value="jd" className="flex-1 sm:flex-none">
              Job description
            </TabsTrigger>
            <TabsTrigger value="apply" className="flex-1 sm:flex-none">
              Apply
            </TabsTrigger>
          </TabsList>

          {/* forceMount keeps BOTH panels mounted: re-reading the JD mid-way
              through the questionnaire never discards entered answers. */}
          <TabsContent value="jd" forceMount hidden={tab !== "jd"} className="mt-4">
            <Section title="About this role" description={subtitle} contentClassName="space-y-7">
                {hasJdContent ? (
                  <>
                    <JdBlock title="Job description" value={jd.description} />
                    <JdBlock title="Role" value={jd.role} />
                    <JdBlock title="Responsibilities" value={jd.responsibilities} />
                    <JdBlock
                      title="Accountabilities"
                      value={jd.accountabilities}
                    />
                    <JdBlock title="Education" value={jd.education} />
                    {asLines(jd.skills).length > 0 ? (
                      <section className="space-y-2">
                        <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-600">
                          Skills
                        </h3>
                        <div className="flex flex-wrap gap-1.5">
                          {asLines(jd.skills).map((s) => (
                            <Badge key={s} variant="secondary">
                              {s}
                            </Badge>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    <section className="space-y-3">
                      <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-brand-600">
                        At a glance
                      </h3>
                      <dl className="grid gap-x-6 gap-y-3 rounded-xl border border-border bg-secondary p-4 text-sm sm:grid-cols-2">
                        <Fact label="Company" value={companyName} />
                        <Fact label="Department" value={job.department} />
                        <Fact label="Level" value={job.level} />
                        <Fact label="Reporting to" value={jdText(jd.reporting_to)} />
                        <Fact label="Reportees" value={jdText(jd.reportees)} />
                        <Fact
                          label="Experience"
                          value={
                            typeof jd.experience_years === "number" &&
                            jd.experience_years > 0
                              ? `${jd.experience_years}+ years`
                              : null
                          }
                        />
                      </dl>
                    </section>
                  </>
                ) : (
                  <p className="text-sm leading-6">
                    The employer has not published a detailed description for
                    this role yet. Reach out to them if you need more context
                    before applying.
                  </p>
                )}
                <Separator />
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm leading-6">
                    The application takes about 10 minutes.
                  </p>
                  <Button type="button" size="lg" onClick={() => setTab("apply")}>
                    Apply for this role
                  </Button>
                </div>
            </Section>
          </TabsContent>

          <TabsContent
            value="apply"
            forceMount
            hidden={tab !== "apply"}
            className="mt-4"
          >
            {!candidateSessionVerified ? (
              <Section
                title={
                  checkingCandidateSession
                    ? "Checking your sign-in"
                    : "Sign in to apply"
                }
                description={
                  user && !isCandidate
                    ? "You are signed in with a non-candidate account. Sign in with a candidate account to continue."
                    : "Reading the job description needs no account. Signing in is only needed to submit."
                }
              >
                {checkingCandidateSession ? (
                  <p role="status" className="flex items-center gap-2 text-sm">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Confirming candidate session
                  </p>
                ) : (
                  <ApplyAuth
                    onAuthed={() => {
                      setCandidateSessionVerified(false);
                      setAuthCheckVersion((version) => version + 1);
                      void refresh();
                    }}
                  />
                )}
              </Section>
            ) : alreadyApplied ? (
              <Card className="shadow-card">
                <CardContent className="flex flex-col items-center p-8 text-center">
                  <span className="grid h-14 w-14 place-items-center rounded-2xl bg-rating-1-bg text-rating-1">
                    <CheckCircle2 className="h-7 w-7" aria-hidden="true" />
                  </span>
                  <h2 className="mt-5 text-base font-semibold">
                    You have already applied to this role
                  </h2>
                  <p className="mt-2 max-w-md text-pretty text-sm leading-6">
                    Your application for {job.title}
                    {appliedAt
                      ? ` was received on ${new Date(appliedAt).toLocaleDateString()}`
                      : " is already on file"}
                    . Follow its progress from your portal.
                  </p>
                  <Button asChild className="mt-6">
                    <a href="/portal/applications">Track my application</a>
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <Section
                title="Your application"
                description="Fields marked * are required. Your answers are kept if you switch back to the job description."
              >
                  <form className="space-y-10" onSubmit={submit} noValidate>
                    {submitError ? (
                      <div ref={errorRef}>
                        <InlineError>{submitError}</InlineError>
                      </div>
                    ) : null}

                    <FormSection
                      title="Personal details"
                      description="As per PF records / Class X memorandum."
                    >
                      <div className="grid gap-4 sm:grid-cols-2">
                        <FormField
                          label="Full name"
                          htmlFor="ap-name"
                          required
                          error={
                            missingPersonal.includes("Full name")
                              ? "Enter your full name."
                              : null
                          }
                        >
                          <Input
                            id="ap-name"
                            autoComplete="name"
                            value={personal.full_name}
                            onChange={(e) =>
                              setPersonal({ ...personal, full_name: e.target.value })
                            }
                          />
                        </FormField>
                        <FormField
                          label="Residing city"
                          htmlFor="ap-city"
                          required
                          error={
                            missingPersonal.includes("Residing city")
                              ? "Enter the city you live in."
                              : null
                          }
                        >
                          <Input
                            id="ap-city"
                            autoComplete="address-level2"
                            value={personal.residing_city}
                            onChange={(e) =>
                              setPersonal({
                                ...personal,
                                residing_city: e.target.value,
                              })
                            }
                          />
                        </FormField>
                        <FormField
                          label="Age"
                          htmlFor="ap-age"
                          required
                          error={
                            missingPersonal.includes("Age")
                              ? "Enter your age."
                              : null
                          }
                        >
                          <Input
                            id="ap-age"
                            type="number"
                            min={16}
                            max={100}
                            value={personal.age}
                            onChange={(e) =>
                              setPersonal({ ...personal, age: e.target.value })
                            }
                          />
                        </FormField>
                        <FormField
                          label="Gender"
                          htmlFor="ap-gender"
                          required
                          error={
                            missingPersonal.includes("Gender")
                              ? "Select an option."
                              : null
                          }
                        >
                          <Select
                            value={personal.gender}
                            onValueChange={(v) =>
                              setPersonal({ ...personal, gender: v })
                            }
                          >
                            <SelectTrigger id="ap-gender">
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
                      description="Reuse the resume already on your record, or upload a fresh one."
                    >
                      <ResumeChoice
                        id="ap-resume"
                        mode={resumeMode}
                        onModeChange={setResumeMode}
                        stored={storedResume}
                        storedLoading={loadingContext}
                        file={resume}
                        progress={uploadProgress}
                        error={resumeError}
                        disabled={busy}
                        onFileChange={(file, error) => {
                          setResume(file);
                          setResumeError(error);
                          setUploadProgress(0);
                        }}
                      />
                    </FormSection>

                    <Separator />

                    <ApplicationValidationForm
                      fields={validationFields}
                      values={validation}
                      onChange={setValidation}
                      disabled={busy}
                    />

                    <Separator />

                    <FormSection
                      title="Questionnaire"
                      description="The 40-aspect questionnaire. Question 40 is your consent to be matched against future roles."
                    >
                      <AspectsForm
                        answers={answers}
                        onChange={setAnswers}
                        excludeIds={COVERED_ASPECT_IDS}
                        invalidIds={missingIds}
                      />
                    </FormSection>

                    <div className="space-y-3">
                      <Button
                        type="submit"
                        size="lg"
                        className="w-full"
                        disabled={busy}
                      >
                        {busy
                          ? uploadProgress > 0 && uploadProgress < 100
                            ? `Uploading ${uploadProgress}%`
                            : "Submitting"
                          : "Submit application"}
                      </Button>
                      <p className="text-center text-xs leading-5">
                        {progress.answered} of {progress.total} questionnaire
                        answers complete.
                      </p>
                    </div>
                  </form>
              </Section>
            )}
          </TabsContent>
        </Tabs>
    </PublicShell>
  );
}

function Fact({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="opacity-80">{label}</dt>
      <dd className="font-semibold">{value}</dd>
    </div>
  );
}

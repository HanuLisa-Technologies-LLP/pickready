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
// mid-application without losing a single answer (both panels stay mounted).
//
// ONE FORM, THE PORTAL'S. The Apply tab is `components/apply-form.tsx`, the
// same component the portal's New Jobs dialog renders, posting
// `application_source=external_link`. This page used to assemble a request of
// its own with a name, a city, a mandatory age and gender and a forty-item
// questionnaire; the server never needed any of it, and a second form is how
// the two surfaces came to ask different things of the same person.
//
// NO STAFF ROUTE IS EVER TRIED. The job is read from the public route, then
// (for a candidate whose session can see a job the public read cannot) from
// the candidate's own route. The organisation's `/jobs/{id}` is not a
// fallback for a candidate page: it is a different audience's API.

import * as React from "react";
import { useParams } from "next/navigation";
import { AlertCircle, Clock, Loader2 } from "lucide-react";

import { apiGet } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { ApplyAuth } from "@/components/apply-auth";
import { ApplyForm } from "@/components/apply-form";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PublicNotice, PublicShell } from "@/components/public-shell";
import { JsonLd, compact } from "@/components/json-ld";
import { Section } from "@/components/page-primitives";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

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

/**
 * The JobPosting payload for this role.
 *
 * EVERY FIELD COMES FROM THE FETCHED JOB, AND NOTHING IS INVENTED. No
 * `baseSalary`, because compensation is stripped from every job fact this
 * product carries. No `employmentType`, `jobLocation` or `validThrough`,
 * because `GET /jobs/public/{id}` returns none of them, and a structured-data
 * field asserting something the record does not say is worse than its absence:
 * it is published, machine-read, and nobody on the hiring team ever sees it.
 *
 * `datePosted` is the job record's own creation timestamp, the only date in
 * the payload. The posting window's start date is not exposed publicly.
 *
 * Returns null when the role has no usable description, so a half-empty
 * posting is never published.
 */
function jobPostingSchema(
  job: PublicJob,
  jd: Record<string, unknown>,
  companyName: string | undefined
): Record<string, unknown> | null {
  const description = [
    jdText(jd.description),
    jdText(jd.role),
    jdText(jd.responsibilities),
  ]
    .filter(Boolean)
    .join(" ");
  if (!description) return null;

  return compact({
    "@context": "https://schema.org",
    "@type": "JobPosting",
    title: job.title,
    description,
    identifier: {
      "@type": "PropertyValue",
      name: companyName ?? "Vivekium",
      value: job.id,
    },
    datePosted: job.created_at ?? undefined,
    hiringOrganization: companyName
      ? { "@type": "Organization", name: companyName }
      : undefined,
    occupationalCategory: job.department ?? undefined,
    skills: asLines(jd.skills).join(", ") || undefined,
    educationRequirements: jdText(jd.education) ?? undefined,
    // The application is submitted on this page, not on a third-party board.
    directApply: true,
    url: `https://readypick.ai/apply/${job.id}`,
  });
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

  // The PUBLIC read comes first so an unauthenticated visitor sees the whole
  // JD before signing in (FR-3.5); the candidate's own route is tried after it
  // because a signed-in candidate may hold a job the public read no longer
  // serves (the grace period for editing an application).
  const loadJob = React.useCallback(async () => {
    setLoadingJob(true);
    setNotFound(false);
    const paths = [`/jobs/public/${jobUuid}`, `/portal/jobs/${jobUuid}`];
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
        // A 404 on the public read and a 401 on the candidate route while
        // signed out are both expected here; the next path is tried and a
        // role no path serves ends in the not-available state below.
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
  // form and only fail after the candidate has filled it in.
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
    [companyName, job.department].filter(Boolean).join(" · ") || "Open role";

  const readMinutes = readTimeMinutes(jd);
  const hasJdContent = Object.values(jd).some((v) => asLines(v).length > 0);

  return (
    <PublicShell>
        {/* Structured data for this role. Built from the fetched job in code
            and serialised with JSON.stringify, so it is not an XSS vector. */}
        <JsonLd data={jobPostingSchema(job, jd, companyName)} />

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
              <p className="mt-2 text-sm">{subtitle}</p>
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
              through the application never discards entered answers. */}
          <TabsContent value="jd" forceMount hidden={tab !== "jd"} className="mt-4">
            {/* HEADING LEVEL, NOT DECORATION. The h1 above is the role; the
                next heading in the document was the card title, which
                `Section` renders through `CardTitle`, which is an h3. That is
                a skipped level, and the fix cannot be made in `CardTitle`
                without changing every card in the product.

                A tab panel is a section of the page and deserves a name at
                level 2 regardless: the tab controls are buttons, so without
                this a screen reader has no heading for either panel. Visually
                hidden because the tab already labels it on screen, which is
                why nothing here changes size or position. */}
            <h2 className="sr-only">Job description</h2>
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
                  <p className="text-sm">
                    The employer has not published a detailed description for
                    this role yet. Reach out to them if you need more context
                    before applying.
                  </p>
                )}
                <Separator />
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm">
                    Applying takes a resume and a few questions about your
                    availability and expectations.
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
            {/* The second panel's level-2 heading. Same reasoning as the JD
                panel above: it names the panel and closes the h1-to-h3 gap. */}
            <h2 className="sr-only">Apply for this role</h2>
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
            ) : (
              <Section
                title="Your application"
                description="Your answers are kept if you switch back to the job description."
              >
                <ApplyForm
                  jobId={job.id}
                  jobTitle={job.title}
                  companyName={companyName}
                  source="external_link"
                />
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
      <dt className="font-normal">{label}</dt>
      <dd className="font-semibold">{value}</dd>
    </div>
  );
}

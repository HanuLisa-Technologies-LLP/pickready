"use client";

// THE application form. One component, used by the portal's New Jobs dialog
// and by the public /apply/{job} page, because there is one apply path on the
// server (`POST /portal/jobs/{id}/apply`) and two screens that each assembled
// their own request are how the public page came to demand an age, a gender
// and a forty-item questionnaire the portal never asked for.
//
// WHAT IT ASKS FOR, AND NOTHING ELSE
// A resume (the main one on the profile, or a fresh upload) and the six
// validation fields, whose definitions the server serves so the form and the
// report's Validation section cannot drift apart. Personal details live on My
// Profile; they are not re-asked per job.
//
// ALREADY APPLIED IS DECIDED BEFORE ANY FIELD RENDERS
// `apply-context` answers it up front. A form that only learned it from a 409
// after a 10 MB upload would have wasted the upload and the typing.
//
// SUBMITTING IS NOT BEING ASSESSED
// The success state points at the application, never at an assessment. The
// hiring team invites candidates to the assessment one by one; sending
// everybody to a questions page would land them on a refusal.

import * as React from "react";
import Link from "next/link";
import { CheckCircle2, Loader2 } from "lucide-react";

import { ApiError, apiGet, apiUploadWithProgress } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import {
  ApplicationValidationForm,
  missingValidationFields,
  type ValidationFieldSpec,
  type ValidationValues,
} from "@/components/application-validation-form";
import {
  ResumeChoice,
  type ResumeMode,
  type StoredResume,
} from "@/components/resume-file-input";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";

/** Where the applicant clicked. Provenance for display only; the server maps
 *  anything else to "direct" and branches on neither. */
export type ApplicationSource = "direct" | "external_link";

/** Mirrors `portal.ApplyContextOut`. */
export interface ApplyContext {
  job_id: string;
  already_applied: boolean;
  applied_at?: string | null;
  /** The existing application's id, when the server sends it. */
  application_id?: string | null;
  resume: StoredResume;
  profile_complete: boolean;
  profile_missing: string[];
  validation_fields?: ValidationFieldSpec[];
  validation_intro?: string;
  validation_values?: ValidationValues;
}

/** Mirrors `schemas.portal.ApplyOut`. Says nothing about an assessment. */
export interface ApplyResult {
  link_id: string;
  job_id: string;
  profile_id: string;
  resume_reused: boolean;
}

/** Where a candidate follows one application. */
export function applicationHref(applicationId?: string | null): string {
  return applicationId
    ? `/portal/applications?application=${encodeURIComponent(applicationId)}`
    : "/portal/applications";
}

type ContextState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; context: ApplyContext };

export function ApplyForm({
  jobId,
  jobTitle,
  companyName,
  source,
  applicationId,
  onSubmitted,
}: {
  jobId: string;
  jobTitle: string;
  companyName?: string | null;
  source: ApplicationSource;
  /** The existing application's id when the caller already knows it (the
   *  New Jobs board carries it on every card). */
  applicationId?: string | null;
  onSubmitted?: (result: ApplyResult) => void;
}) {
  const [state, setState] = React.useState<ContextState>({ kind: "loading" });
  const [attempt, setAttempt] = React.useState(0);
  const [resumeMode, setResumeMode] = React.useState<ResumeMode>("upload");
  const [resume, setResume] = React.useState<File | null>(null);
  const [validation, setValidation] = React.useState<ValidationValues>({});
  const [busy, setBusy] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [resumeError, setResumeError] = React.useState<string | null>(null);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [submitted, setSubmitted] = React.useState<ApplyResult | null>(null);

  React.useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    apiGet<ApplyContext>(`/portal/jobs/${jobId}/apply-context`)
      .then((context) => {
        if (!active) return;
        setState({ kind: "ready", context });
        setResumeMode(context.resume.has_resume ? "reuse" : "upload");
        // Carried forward from the last application. Merged UNDER anything
        // already typed, so a late answer cannot overwrite an edit.
        const carried = context.validation_values ?? {};
        if (Object.keys(carried).length > 0) {
          setValidation((current) => ({ ...carried, ...current }));
        }
      })
      .catch((failure) => {
        // Said, never papered over: without the context the form cannot know
        // whether this person already applied or has a resume to reuse.
        if (active) setState({ kind: "error", message: apiErrorMessage(failure) });
      });
    return () => {
      active = false;
    };
  }, [jobId, attempt]);

  if (state.kind === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Checking your application details
      </p>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="space-y-3">
        <InlineError>
          We could not load your application details. {state.message}
        </InlineError>
        <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>
          Try again
        </Button>
      </div>
    );
  }

  const context = state.context;
  const where = companyName ? `${jobTitle} at ${companyName}` : jobTitle;

  if (submitted) {
    return (
      <Outcome
        title="Application submitted"
        body={`Thanks for applying to ${where}. The hiring team invites candidates to the assessment individually, and you will be emailed if they invite you.`}
        href={applicationHref(submitted.link_id)}
        action="View your application"
      />
    );
  }

  if (context.already_applied) {
    const when = context.applied_at
      ? ` on ${new Date(context.applied_at).toLocaleDateString()}`
      : "";
    return (
      <Outcome
        title="You have already applied to this role"
        body={`Your application for ${where} was received${when}. Follow its progress under Applied Jobs.`}
        href={applicationHref(context.application_id ?? applicationId)}
        action="View your application"
      />
    );
  }

  const fields = context.validation_fields ?? [];

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (resumeMode === "upload" && !resume) {
      setResumeError("Attach a PDF or DOCX resume (up to 10 MB).");
      return;
    }
    if (resumeMode === "reuse" && !context.resume.has_resume) {
      setResumeError("There is no main resume on your profile yet. Upload one instead.");
      return;
    }
    // Checked BEFORE the upload starts: a refusal after a 10 MB resume has
    // already been sent is a bad way to learn a dropdown was missed.
    const missing = missingValidationFields(fields, validation);
    if (missing.length > 0) {
      setSubmitError(`Please complete every required field: ${missing.join(", ")}.`);
      return;
    }
    setBusy(true);
    setProgress(0);
    setResumeError(null);
    setSubmitError(null);
    try {
      const form = new FormData();
      if (resumeMode === "reuse") {
        form.append("reuse_previous", "true");
      } else if (resume) {
        form.append("resume", resume);
      }
      form.append("validation", JSON.stringify(validation));
      form.append("application_source", source);
      const result = await apiUploadWithProgress<ApplyResult>(
        `/portal/jobs/${jobId}/apply`,
        form,
        setProgress,
      );
      setSubmitted(result);
      onSubmitted?.(result);
    } catch (failure) {
      const message = apiErrorMessage(failure);
      if (failure instanceof ApiError && failure.status === 409) {
        // A 409 is either "already applied" (another tab, or a double submit,
        // got there first) or "applications have closed". The server's context
        // is asked which, rather than the sentence being pattern-matched. If
        // that read fails as well (a closed posting answers 404), the
        // server's own 409 sentence is what the candidate is shown below.
        const fresh = await apiGet<ApplyContext>(
          `/portal/jobs/${jobId}/apply-context`,
        ).catch(() => null);
        if (fresh?.already_applied) {
          setState({ kind: "ready", context: fresh });
          return;
        }
      }
      setSubmitError(message);
      if (resumeMode === "upload") setResumeError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="space-y-6" onSubmit={submit} noValidate>
      {!context.profile_complete ? (
        <div className="rounded-xl border border-border bg-brand-100/50 p-4 text-sm">
          <p className="font-semibold">Your profile is not complete yet.</p>
          <p className="mt-1">
            Employers see your profile answers alongside this application. You
            can still apply now, then{" "}
            <Link
              className="font-semibold text-brand-600 underline underline-offset-4"
              href="/portal/profile"
            >
              complete My Profile
            </Link>
            .
          </p>
        </div>
      ) : null}

      <ResumeChoice
        id={`apply-resume-${jobId}`}
        mode={resumeMode}
        onModeChange={setResumeMode}
        stored={context.resume}
        file={resume}
        progress={progress}
        error={resumeError}
        disabled={busy}
        onFileChange={(file, error) => {
          setResume(file);
          setResumeError(error);
          setProgress(0);
        }}
      />

      <ApplicationValidationForm
        fields={fields}
        values={validation}
        onChange={setValidation}
        disabled={busy}
        intro={context.validation_intro}
      />

      {submitError ? <InlineError>{submitError}</InlineError> : null}

      <Button type="submit" size="lg" className="w-full" disabled={busy}>
        {busy
          ? progress > 0 && progress < 100
            ? `Uploading ${progress}%`
            : "Submitting"
          : "Submit application"}
      </Button>
    </form>
  );
}

function Outcome({
  title,
  body,
  href,
  action,
}: {
  title: string;
  body: string;
  href: string;
  action: string;
}) {
  return (
    <div
      role="status"
      className="flex flex-col items-center rounded-xl border border-border p-6 text-center"
    >
      <CheckCircle2 className="h-7 w-7 text-brand-600" aria-hidden="true" />
      <h3 className="mt-3 text-base font-semibold">{title}</h3>
      <p className="mt-2 max-w-md text-pretty text-sm">{body}</p>
      <Button asChild className="mt-5">
        <Link href={href}>{action}</Link>
      </Button>
    </div>
  );
}

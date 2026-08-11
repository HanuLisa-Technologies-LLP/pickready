"use client";

/**
 * The assessment invitation landing page.
 *
 * Every assessment email now points here, at a signed token, rather than at
 * `/portal/assessments/<application id>`. This page owns exactly one decision:
 * where the candidate goes next. It never renders an assessment itself.
 *
 * Why it is a page and not a backend redirect: the interesting states are not
 * redirects. "You are signed in as someone else" and "you already submitted
 * this" need to be READ, and a 302 has nowhere to put an explanation. So the
 * backend resolves the token to a state (`GET /assessments/invitations/{token}`)
 * and this page renders or forwards accordingly.
 *
 * It is deliberately outside every portal shell. The shell requires a session,
 * and requiring a session here would mean bouncing to /login before the token
 * had been read at all, which is the behaviour this whole change replaces.
 */

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { CheckCircle2, Clock, Loader2, LogIn, ShieldAlert } from "lucide-react";

import { apiGet } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { AuthShell } from "@/components/auth-shell";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";

interface InvitationResolve {
  state:
    | "needs_auth"
    | "wrong_account"
    | "ready"
    | "in_progress"
    | "completed"
    | "not_invited"
    | "expired"
    | "window_closed"
    | "invalid"
    | "gone";
  redirect_to: string | null;
  invited_email_masked: string | null;
  signed_in_email: string | null;
  job_title: string | null;
  company_name: string | null;
  message: string;
  recent_prior_report: boolean;
}

/** States that mean "go there now" rather than "read this". */
const FORWARDING = new Set(["ready", "in_progress", "completed"]);

export default function AssessmentInvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { logout } = useAuth();
  const [result, setResult] = React.useState<InvitationResolve | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // The path we send to /login as `next`, so sign-in comes straight back here
  // and this component resolves the token again with a session attached. It is
  // this page rather than the assessment itself on purpose: the wrong-account
  // and already-submitted checks have to run AFTER authentication, and sending
  // them to the assessment directly would skip them.
  const selfPath = `/assessments/invite/${token}`;

  React.useEffect(() => {
    let cancelled = false;
    // The assessments router is mounted at /api/v2 ONLY (backend/app/main.py).
    // `API_BASE` defaults to /api/v1, so a relative path here would 404 -- the
    // same mount-versus-path drift that broke resume previews on 2026-08-09.
    // Written in full, exactly as the conversation endpoints beside it are.
    apiGet<InvitationResolve>(`/api/v2/assessments/invitations/${token}`)
      .then((data) => {
        if (cancelled) return;
        if (data.state === "needs_auth") {
          router.replace(
            `/login?portal=candidate&next=${encodeURIComponent(selfPath)}`
          );
          return;
        }
        // One exception to forwarding: a candidate who was assessed for
        // another role inside the last six months is owed the reason they are
        // answering questions again before they start typing. Under PPI the
        // framework comes from each job's own JD, so nothing carries over --
        // but silently reasking is what makes that feel like a bug.
        const explainRetake =
          data.recent_prior_report && data.state === "ready";
        if (FORWARDING.has(data.state) && data.redirect_to && !explainRetake) {
          router.replace(data.redirect_to);
          return;
        }
        setResult(data);
      })
      .catch((cause) => {
        if (cancelled) return;
        // Surface the real cause. A bare "something went wrong" on the one
        // page standing between a candidate and their assessment is the exact
        // failure this codebase keeps having to undo.
        setError(
          cause instanceof Error
            ? cause.message
            : "We could not read this invitation link."
        );
      });
    return () => {
      cancelled = true;
    };
  }, [token, router, selfPath]);

  if (error) {
    return (
      <AuthShell title="This invitation could not be opened">
        <InlineError>{error}</InlineError>
        <Button asChild variant="outline" className="mt-6 w-full">
          <Link href="/portal">Go to your applications</Link>
        </Button>
      </AuthShell>
    );
  }

  // Resolving, or forwarding. Either way the honest thing to show is that
  // something is happening, not an empty page.
  if (!result) {
    return (
      <AuthShell title="Opening your assessment">
        <p className="flex items-center justify-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          One moment.
        </p>
      </AuthShell>
    );
  }

  const forWhat = result.job_title
    ? `${result.job_title}${
        result.company_name ? ` at ${result.company_name}` : ""
      }`
    : null;

  if (result.state === "wrong_account") {
    return (
      <AuthShell
        title="Signed in with a different account"
        description={forWhat ?? undefined}
      >
        <div className="space-y-5">
          <p className="flex items-start gap-2.5 text-sm leading-6">
            <ShieldAlert
              className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
              aria-hidden="true"
            />
            {result.message}
          </p>
          <dl className="space-y-2 rounded-xl border border-field bg-surface p-4 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="font-medium">Invitation sent to</dt>
              <dd>{result.invited_email_masked}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="font-medium">Signed in as</dt>
              <dd className="truncate">{result.signed_in_email}</dd>
            </div>
          </dl>
          <Button
            className="w-full"
            onClick={() => {
              // Sign out, then come back here. Resolving again with the right
              // account is what actually opens the assessment, so the return
              // trip has to be through this page.
              void logout().finally(() => {
                window.location.href = `/login?portal=candidate&next=${encodeURIComponent(
                  selfPath
                )}`;
              });
            }}
          >
            <LogIn className="h-4 w-4" aria-hidden="true" />
            Sign in with the invited account
          </Button>
        </div>
      </AuthShell>
    );
  }

  if (result.recent_prior_report && result.redirect_to) {
    return (
      <AuthShell
        title="A fresh assessment for this role"
        description={forWhat ?? undefined}
      >
        <p className="text-sm leading-6">
          You completed an assessment for another role recently. Each role is
          evaluated against criteria written from its own job description, so
          nothing carries across and this one starts fresh. Your answers save as
          you go.
        </p>
        <Button asChild className="mt-6 w-full">
          <Link href={result.redirect_to}>Start the assessment</Link>
        </Button>
      </AuthShell>
    );
  }

  if (result.state === "completed") {
    return (
      <AuthShell
        title="You have already sent this one"
        description={forWhat ?? undefined}
      >
        <p className="flex items-start gap-2.5 text-sm leading-6">
          <CheckCircle2
            className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
            aria-hidden="true"
          />
          {result.message}
        </p>
        <Button asChild className="mt-6 w-full">
          <Link href={result.redirect_to ?? "/portal/applications"}>
            View your application
          </Link>
        </Button>
      </AuthShell>
    );
  }

  const title =
    result.state === "expired"
      ? "This link has expired"
      : result.state === "window_closed"
        ? "This job posting has closed"
        : result.state === "not_invited"
          ? "Not open yet"
          : "This invitation could not be opened";

  return (
    <AuthShell title={title} description={forWhat ?? undefined}>
      <p className="flex items-start gap-2.5 text-sm leading-6">
        <Clock
          className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
          aria-hidden="true"
        />
        {result.message}
      </p>
      <Button asChild variant="outline" className="mt-6 w-full">
        <Link href="/portal/applications">Go to your applications</Link>
      </Button>
    </AuthShell>
  );
}

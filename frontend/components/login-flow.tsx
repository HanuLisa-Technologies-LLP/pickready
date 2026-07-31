"use client";

import * as React from "react";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { signInWithEmailAndPassword, signInWithPopup } from "firebase/auth";
import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";

import { ApiError } from "@/lib/api";
import { createCandidateGoogleProvider, firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  ROLE_LABEL,
  selectContext,
  type FirebaseExchangeResult,
  type RequestedPortal,
} from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import type { AuthContextsResponse, AuthSession } from "@/lib/types";
import { AuthDivider, AuthLink, AuthShell } from "@/components/auth-shell";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { GoogleMark } from "@/components/google-mark";

export function LoginFlow({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setSession } = useAuth();
  const initialPortal = searchParams.get("portal");
  const [requestedPortal, setRequestedPortal] = React.useState<RequestedPortal | null>(
    initialPortal === "candidate" ||
      initialPortal === "org" ||
      initialPortal === "bd" ||
      initialPortal === "owner"
      ? initialPortal
      : null
  );
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [contexts, setContexts] = React.useState<AuthContextsResponse | null>(
    null
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const finish = React.useCallback(
    (session: AuthSession) => {
      setSession(session.user, session.capabilities ?? []);
      const requested =
        typeof window === "undefined"
          ? null
          : new URLSearchParams(window.location.search).get("next");
      const safeNext =
        requested && requested.startsWith("/") && !requested.startsWith("//")
          ? requested
          : null;
      router.replace(safeNext ?? homePathForRole(session.user.role));
    },
    [router, setSession]
  );

  const resolve = React.useCallback(
    (result: FirebaseExchangeResult) => {
      if (isContextsResponse(result)) {
        setContexts(result);
        return;
      }
      finish(result);
    },
    [finish]
  );

  const run = (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    void action()
      .catch((authError) => {
        const message = friendlyAuthError(authError);
        if (message) setError(message);
      })
      .finally(() => setBusy(false));
  };

  const passwordSignIn = (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    run(async () => {
      const credential = await signInWithEmailAndPassword(
        firebaseAuth,
        email.trim(),
        password
      );
      resolve(await exchangeFirebaseSession(credential.user, requestedPortal));
    });
  };

  const googleSignIn = () =>
    run(async () => {
      const credential = await signInWithPopup(
        firebaseAuth,
        createCandidateGoogleProvider()
      );
      resolve(await exchangeFirebaseSession(credential.user, requestedPortal));
    });

  /**
   * Finalize the workspace choice.
   *
   * A `context_token` is single-use and short-lived: the backend answers 410
   * once it has been spent and 401 once it has expired. Either way the token in
   * React state is dead, and re-clicking a workspace used to fail forever, the
   * visible symptom being "410 Gone, and re-logging in doesn't work". The
   * Firebase sign-in is still valid at this point, so we mint a FRESH context
   * token from it and retry the same choice once, transparently.
   */
  const chooseContext = (userId: string) =>
    run(async () => {
      if (!contexts) return;
      try {
        finish(await selectContext(contexts.context_token, userId));
        return;
      } catch (error) {
        const spent =
          error instanceof ApiError &&
          (error.status === 410 || error.status === 401);
        if (!spent) throw error;
      }

      const account = firebaseAuth.currentUser;
      if (!account) {
        // Nothing left to re-mint from, send them back to a clean sign-in
        // rather than leaving a chooser whose buttons all fail.
        setContexts(null);
        setError("That sign-in has expired. Please sign in again.");
        return;
      }
      const retry = await exchangeFirebaseSession(account, requestedPortal);
      if (!isContextsResponse(retry)) {
        // The account resolves to a single workspace now, that IS the session.
        finish(retry);
        return;
      }
      setContexts(retry);
      finish(await selectContext(retry.context_token, userId));
    });

  return (
    <AuthShell
      title={contexts ? "Choose your workspace" : title}
      description={
        contexts
          ? "This email belongs to more than one PickReady workspace."
          : description
      }
      footer={
        contexts ? null : (
          <>
            Need an account? <AuthLink href="/register">Create one</AuthLink>
          </>
        )
      }
    >
      {contexts ? (
        <div className="space-y-3">
          {contexts.contexts.map((context) => (
            <button
              key={context.user_id}
              type="button"
              disabled={busy}
              onClick={() => chooseContext(context.user_id)}
              className="w-full rounded-xl border border-border bg-surface px-4 py-3 text-left shadow-card transition-colors duration-150 hover:border-brand-600/50 hover:bg-brand-100/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
            >
              <span className="block text-sm font-semibold">
                {context.tenant_name ?? "PickReady"}
              </span>
              <span className="mt-0.5 block text-xs opacity-80">
                {ROLE_LABEL[context.role]}
              </span>
            </button>
          ))}
          <Button
            variant="ghost"
            className="w-full"
            disabled={busy}
            onClick={() => setContexts(null)}
          >
            Use a different account
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <PortalChooser value={requestedPortal} onChange={setRequestedPortal} />

          <Button
            type="button"
            variant="outline"
            size="lg"
            className="w-full"
            disabled={busy}
            onClick={googleSignIn}
          >
            <GoogleMark />
            Continue with Google
          </Button>

          <AuthDivider />

          <form className="space-y-4" onSubmit={passwordSignIn}>
            <div className="space-y-1.5">
              <Label htmlFor="login-email">Email address</Label>
              <Input
                id="login-email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                value={email}
                disabled={busy}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="login-password">Password</Label>
              <div className="relative">
                <Input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  disabled={busy}
                  className="pr-11"
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
                <button
                  type="button"
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden="true" />
                  )}
                </button>
              </div>
            </div>
            <Button size="lg" className="w-full" disabled={busy}>
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Signing in
                </>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>
        </div>
      )}

      {error ? <InlineError>{error}</InlineError> : null}
    </AuthShell>
  );
}

const PORTALS: Array<{
  value: RequestedPortal | null;
  label: string;
  detail: string;
}> = [
  { value: null, label: "Find my workspace", detail: "Show every workspace linked to this account" },
  { value: "candidate", label: "Candidate", detail: "Applications, profile and assessments" },
  { value: "org", label: "Company team", detail: "Jobs, candidates and decision workflow" },
  { value: "bd", label: "Business development", detail: "PickReady leads and customer reach" },
  { value: "owner", label: "Provider owner", detail: "Customers, billing and platform controls" },
];

function PortalChooser({
  value,
  onChange,
}: {
  value: RequestedPortal | null;
  onChange: (value: RequestedPortal | null) => void;
}) {
  return (
    <fieldset>
      <legend className="text-sm font-semibold">Choose your workspace</legend>
      <p className="mt-1 text-xs leading-5">
        Your account invitation still decides what you can access.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {PORTALS.map((portal) => {
          const selected = portal.value === value;
          return (
            <button
              key={portal.label}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(portal.value)}
              className={`rounded-xl border px-3 py-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                selected
                  ? "border-brand-600 bg-brand-100/70"
                  : "border-border bg-surface hover:border-brand-600/40"
              } ${portal.value === null ? "sm:col-span-2" : ""}`}
            >
              <span className="block text-xs font-semibold">{portal.label}</span>
              <span className="mt-0.5 block text-[11px] leading-4">{portal.detail}</span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

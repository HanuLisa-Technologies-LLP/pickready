"use client";

import * as React from "react";
import Link from "next/link";
import {
  GoogleAuthProvider,
  RecaptchaVerifier,
  signInWithEmailAndPassword,
  signInWithPhoneNumber,
  signInWithPopup,
  type ConfirmationResult,
} from "firebase/auth";
import { useRouter } from "next/navigation";

import { firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  ROLE_LABEL,
  selectContext,
  type FirebaseExchangeResult,
} from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";
import type { AuthContextsResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OtpInput } from "@/components/otp-input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Method = "password" | "phone";

const RECAPTCHA_CONTAINER_ID = "firebase-recaptcha";

function Wordmark() {
  return (
    <div className="mb-2 text-center">
      <span className="text-2xl font-bold tracking-tight text-foreground">
        PickReady
      </span>
    </div>
  );
}

export function LoginFlow({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  const router = useRouter();
  const { setSession } = useAuth();

  const [candidate, setCandidate] = React.useState(true);
  const [method, setMethod] = React.useState<Method>("password");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [confirmation, setConfirmation] =
    React.useState<ConfirmationResult | null>(null);
  const [contexts, setContexts] = React.useState<AuthContextsResponse | null>(
    null
  );
  const [busy, setBusy] = React.useState(false);
  const [redirecting, setRedirecting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const verifier = React.useRef<RecaptchaVerifier | null>(null);

  React.useEffect(
    () => () => {
      verifier.current?.clear();
      verifier.current = null;
    },
    []
  );

  const go = React.useCallback(
    (session: Extract<FirebaseExchangeResult, { user: unknown }>) => {
      setSession(session.user, session.capabilities ?? []);
      setRedirecting(true);
      router.replace(homePathForRole(session.user.role));
    },
    [router, setSession]
  );

  const handleExchange = React.useCallback(
    (result: FirebaseExchangeResult) => {
      if (isContextsResponse(result)) {
        setContexts(result);
        return;
      }
      go(result);
    },
    [go]
  );

  // Single guarded runner: prevents double-submit and maps every failure to
  // clean copy (never a stack trace). A rejected backend exchange also clears
  // the lingering Firebase session so a retry starts clean.
  const run = React.useCallback((action: () => Promise<void>) => {
    setBusy((current) => {
      if (current) return current; // already running — ignore re-entry
      setError(null);
      void (async () => {
        try {
          await action();
        } catch (err) {
          if (err instanceof ApiError) {
            void firebaseAuth.signOut().catch(() => {});
          }
          const message = friendlyAuthError(err);
          if (message) setError(message);
        } finally {
          setBusy(false);
        }
      })();
      return true;
    });
  }, []);

  const signInGoogle = () =>
    run(async () => {
      const cred = await signInWithPopup(
        firebaseAuth,
        new GoogleAuthProvider()
      );
      handleExchange(await exchangeFirebaseSession(cred.user));
    });

  const signInPassword = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const cred = await signInWithEmailAndPassword(
        firebaseAuth,
        email.trim(),
        password
      );
      handleExchange(await exchangeFirebaseSession(cred.user));
    });
  };

  const sendPhoneCode = () =>
    run(async () => {
      verifier.current?.clear();
      verifier.current = new RecaptchaVerifier(
        firebaseAuth,
        RECAPTCHA_CONTAINER_ID,
        { size: "invisible" }
      );
      setCode("");
      setConfirmation(
        await signInWithPhoneNumber(firebaseAuth, phone.trim(), verifier.current)
      );
    });

  const confirmPhoneCode = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      if (!confirmation) return;
      const cred = await confirmation.confirm(code);
      handleExchange(await exchangeFirebaseSession(cred.user));
    });
  };

  const resetPhone = () => {
    setConfirmation(null);
    setCode("");
    setError(null);
  };

  const chooseContext = (userId: string) =>
    run(async () => {
      if (!contexts) return;
      go(await selectContext(contexts.context_token, userId));
    });

  if (redirecting) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <Wordmark />
            <p className="animate-pulse text-sm text-muted-foreground" role="status">
              Loading your workspace…
            </p>
          </CardContent>
        </Card>
        <div id={RECAPTCHA_CONTAINER_ID} aria-hidden="true" />
      </div>
    );
  }

  // Multi-workspace identity: reuse the "choose your workspace" chooser.
  if (contexts) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <Wordmark />
            <CardTitle>Choose your workspace</CardTitle>
            <CardDescription>
              Your account has access to more than one workspace. Pick one to
              continue.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {contexts.contexts.map((ctx) => (
              <Button
                key={ctx.user_id}
                type="button"
                variant="outline"
                className="h-auto w-full flex-col items-start gap-0.5 py-3 text-left"
                disabled={busy}
                onClick={() => chooseContext(ctx.user_id)}
              >
                <span className="font-medium">
                  {ctx.tenant_name ?? "PickReady"}
                </span>
                <span className="text-xs text-muted-foreground">
                  {ROLE_LABEL[ctx.role] ?? ctx.role}
                </span>
              </Button>
            ))}
            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              disabled={busy}
              onClick={() => setContexts(null)}
            >
              Back
            </Button>
          </CardContent>
        </Card>
        <div id={RECAPTCHA_CONTAINER_ID} aria-hidden="true" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <Wordmark />
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            className="grid grid-cols-2 gap-2"
            role="group"
            aria-label="Account type"
          >
            <Button
              type="button"
              variant={candidate ? "default" : "outline"}
              aria-pressed={candidate}
              onClick={() => setCandidate(true)}
            >
              Candidate
            </Button>
            <Button
              type="button"
              variant={!candidate ? "default" : "outline"}
              aria-pressed={!candidate}
              onClick={() => setCandidate(false)}
            >
              Team member
            </Button>
          </div>

          {/* Google — prominent on the candidate path (candidate-only server-side). */}
          {candidate ? (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              disabled={busy}
              onClick={signInGoogle}
            >
              Continue with Google
            </Button>
          ) : null}

          {candidate ? (
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">or</span>
              <span className="h-px flex-1 bg-border" />
            </div>
          ) : null}

          <div
            className="grid grid-cols-2 gap-2"
            role="group"
            aria-label="Sign-in method"
          >
            <Button
              type="button"
              variant={method === "password" ? "secondary" : "outline"}
              aria-pressed={method === "password"}
              onClick={() => setMethod("password")}
            >
              Email &amp; password
            </Button>
            <Button
              type="button"
              variant={method === "phone" ? "secondary" : "outline"}
              aria-pressed={method === "phone"}
              onClick={() => setMethod("phone")}
            >
              Phone
            </Button>
          </div>

          {method === "password" ? (
            <form className="space-y-3" onSubmit={signInPassword}>
              <div className="space-y-1">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>
              <Button className="w-full" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </Button>
            </form>
          ) : !confirmation ? (
            <div className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="phone">Mobile number</Label>
                <Input
                  id="phone"
                  type="tel"
                  autoComplete="tel"
                  placeholder="+91 98765 43210"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Include your country code. We&apos;ll text you a 6-digit code.
                </p>
              </div>
              <Button
                type="button"
                className="w-full"
                onClick={sendPhoneCode}
                disabled={busy || !phone.trim()}
              >
                {busy ? "Sending…" : "Send verification code"}
              </Button>
            </div>
          ) : (
            <form className="space-y-3" onSubmit={confirmPhoneCode}>
              <div className="space-y-1">
                <Label htmlFor="sms-code">Verification code</Label>
                <OtpInput
                  length={6}
                  value={code}
                  onChange={setCode}
                  disabled={busy}
                  invalid={Boolean(error)}
                  autoFocus
                />
              </div>
              <Button className="w-full" disabled={busy || code.length < 6}>
                {busy ? "Verifying…" : "Verify and sign in"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                disabled={busy}
                onClick={resetPhone}
              >
                Use a different number
              </Button>
            </form>
          )}

          {!candidate ? (
            <p className="text-center text-xs text-muted-foreground">
              Google sign-in is available to candidates only.
            </p>
          ) : null}

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          <p className="text-center text-xs text-muted-foreground">
            New candidate?{" "}
            <Link className="underline" href="/register">
              Create an account
            </Link>
          </p>

          {/* Invisible reCAPTCHA host for Firebase Phone Auth. */}
          <div id={RECAPTCHA_CONTAINER_ID} aria-hidden="true" />
        </CardContent>
      </Card>
    </div>
  );
}

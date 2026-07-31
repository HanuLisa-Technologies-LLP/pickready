"use client";

import * as React from "react";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import {
  createUserWithEmailAndPassword,
  signInWithPopup,
  updateProfile,
} from "firebase/auth";
import { useRouter } from "next/navigation";

import { createCandidateGoogleProvider, firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  ROLE_LABEL,
  selectContext,
  type FirebaseExchangeResult,
} from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import type { AuthContextsResponse, AuthSession } from "@/lib/types";
import { AuthDivider, AuthLink, AuthShell } from "@/components/auth-shell";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { GoogleMark } from "@/components/google-mark";
import {
  PasswordRules,
  isPasswordValid,
  passwordRules,
} from "@/components/password-rules";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function RegisterFlow() {
  const router = useRouter();
  const { setSession } = useAuth();
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [contexts, setContexts] = React.useState<AuthContextsResponse | null>(
    null
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const rules = passwordRules(password);
  const passwordValid = isPasswordValid(rules);

  const finish = React.useCallback(
    (session: AuthSession) => {
      setSession(session.user, session.capabilities ?? []);
      router.replace(homePathForRole(session.user.role));
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

  const registerPassword = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }
    if (!EMAIL_RE.test(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    if (!passwordValid) {
      setError(
        "Use at least 8 characters with uppercase, lowercase, and a number."
      );
      return;
    }
    run(async () => {
      const credential = await createUserWithEmailAndPassword(
        firebaseAuth,
        email.trim(),
        password
      );
      await updateProfile(credential.user, { displayName: name.trim() });
      resolve(await exchangeFirebaseSession(credential.user));
    });
  };

  const registerGoogle = () =>
    run(async () => {
      const credential = await signInWithPopup(
        firebaseAuth,
        createCandidateGoogleProvider()
      );
      resolve(await exchangeFirebaseSession(credential.user));
    });

  const chooseContext = (userId: string) =>
    run(async () => {
      if (!contexts) return;
      finish(await selectContext(contexts.context_token, userId));
    });

  return (
    <AuthShell
      title={contexts ? "Choose your workspace" : "Create your account"}
      description={
        contexts
          ? "Your invited email belongs to more than one workspace."
          : "Use your invited email if you are joining a company team."
      }
      footer={
        contexts ? null : (
          <>
            Already registered? <AuthLink href="/login">Sign in</AuthLink>
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
        </div>
      ) : (
        <div className="space-y-5">
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="w-full"
            disabled={busy}
            onClick={registerGoogle}
          >
            <GoogleMark />
            Continue with Google
          </Button>

          <AuthDivider />

          <form className="space-y-4" onSubmit={registerPassword}>
            <div className="space-y-1.5">
              <Label htmlFor="register-name">Full name</Label>
              <Input
                id="register-name"
                autoComplete="name"
                placeholder="Priya Sharma"
                value={name}
                disabled={busy}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="register-email">Email address</Label>
              <Input
                id="register-email"
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
              <Label htmlFor="register-password">Password</Label>
              <div className="relative">
                <Input
                  id="register-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  value={password}
                  disabled={busy}
                  className="pr-11"
                  aria-describedby="password-requirements"
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
              <PasswordRules id="password-requirements" rules={rules} />
            </div>
            <Button
              size="lg"
              className="w-full"
              disabled={busy || !passwordValid}
            >
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Creating account
                </>
              ) : (
                "Create account"
              )}
            </Button>
          </form>
        </div>
      )}

      {error ? <InlineError>{error}</InlineError> : null}
    </AuthShell>
  );
}

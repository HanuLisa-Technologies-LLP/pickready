"use client";

// Inline candidate auth for the PUBLIC job-application flow (PRD v1.0
// FR-3.4/3.5/9.1). Reuses the existing Firebase auth (Google / email+password /
// phone) but — unlike LoginFlow/RegisterFlow — does NOT redirect on success.
// Instead it stores the session and calls onAuthed(), so the applicant stays on
// the public job page and continues straight into the questionnaire.

import * as React from "react";
import {
  RecaptchaVerifier,
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signInWithPhoneNumber,
  signInWithPopup,
  updateProfile,
  type ConfirmationResult,
} from "firebase/auth";

import { createCandidateGoogleProvider, firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  type FirebaseExchangeResult,
} from "@/lib/firebase-session";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OtpInput } from "@/components/otp-input";

type Mode = "signin" | "register";
type Method = "password" | "phone";

const RECAPTCHA_CONTAINER_ID = "firebase-recaptcha-apply";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;

export function ApplyAuth({ onAuthed }: { onAuthed: () => void }) {
  const { setSession } = useAuth();

  const [mode, setMode] = React.useState<Mode>("signin");
  const [method, setMethod] = React.useState<Method>("password");
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [confirmation, setConfirmation] =
    React.useState<ConfirmationResult | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const verifier = React.useRef<RecaptchaVerifier | null>(null);

  React.useEffect(
    () => () => {
      verifier.current?.clear();
      verifier.current = null;
    },
    []
  );

  // Only a candidate identity may apply. Anything else already belongs to a
  // staff/owner account — sign it back out and steer them to normal sign-in.
  const handleExchange = React.useCallback(
    (result: FirebaseExchangeResult) => {
      if (isContextsResponse(result) || result.user.role !== "candidate") {
        void firebaseAuth.signOut().catch(() => {});
        setError(
          "That account isn't a candidate account. Please use a candidate sign-in."
        );
        return;
      }
      setSession(result.user, result.capabilities ?? []);
      onAuthed();
    },
    [onAuthed, setSession]
  );

  const run = React.useCallback((action: () => Promise<void>) => {
    setBusy((current) => {
      if (current) return current;
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

  const google = () =>
    run(async () => {
      const cred = await signInWithPopup(
        firebaseAuth,
        createCandidateGoogleProvider()
      );
      handleExchange(await exchangeFirebaseSession(cred.user));
    });

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }
    if (mode === "register") {
      if (!name.trim()) {
        setError("Enter your full name.");
        return;
      }
      if (password.length < MIN_PASSWORD) {
        setError(`Choose a password with at least ${MIN_PASSWORD} characters.`);
        return;
      }
      run(async () => {
        const cred = await createUserWithEmailAndPassword(
          firebaseAuth,
          trimmedEmail,
          password
        );
        await updateProfile(cred.user, { displayName: name.trim() });
        handleExchange(await exchangeFirebaseSession(cred.user));
      });
      return;
    }
    run(async () => {
      const cred = await signInWithEmailAndPassword(
        firebaseAuth,
        trimmedEmail,
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
      if (mode === "register" && name.trim() && !cred.user.displayName) {
        await updateProfile(cred.user, { displayName: name.trim() });
      }
      handleExchange(await exchangeFirebaseSession(cred.user));
    });
  };

  const resetPhone = () => {
    setConfirmation(null);
    setCode("");
    setError(null);
  };

  return (
    <div className="space-y-4">
      <div
        className="grid grid-cols-2 gap-2"
        role="group"
        aria-label="Account action"
      >
        <Button
          type="button"
          variant={mode === "signin" ? "default" : "outline"}
          aria-pressed={mode === "signin"}
          onClick={() => setMode("signin")}
        >
          Sign in
        </Button>
        <Button
          type="button"
          variant={mode === "register" ? "default" : "outline"}
          aria-pressed={mode === "register"}
          onClick={() => setMode("register")}
        >
          Create account
        </Button>
      </div>

      <Button
        type="button"
        variant="outline"
        className="w-full"
        disabled={busy}
        onClick={google}
      >
        Continue with Google
      </Button>

      <div className="flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="text-xs text-muted-foreground">or</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <div
        className="grid grid-cols-2 gap-2"
        role="group"
        aria-label="Method"
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

      {mode === "register" ? (
        <div className="space-y-1">
          <Label htmlFor="apply-name">Full name</Label>
          <Input
            id="apply-name"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
      ) : null}

      {method === "password" ? (
        <form className="space-y-3" onSubmit={submitPassword}>
          <div className="space-y-1">
            <Label htmlFor="apply-email">Email address</Label>
            <Input
              id="apply-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="apply-password">Password</Label>
            <Input
              id="apply-password"
              type="password"
              autoComplete={
                mode === "register" ? "new-password" : "current-password"
              }
              minLength={mode === "register" ? MIN_PASSWORD : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {mode === "register" ? (
              <p className="text-xs text-muted-foreground">
                At least {MIN_PASSWORD} characters.
              </p>
            ) : null}
          </div>
          <Button className="w-full" disabled={busy}>
            {busy
              ? "Please wait…"
              : mode === "register"
                ? "Create account & continue"
                : "Sign in & continue"}
          </Button>
        </form>
      ) : !confirmation ? (
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="apply-phone">Mobile number</Label>
            <Input
              id="apply-phone"
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
            <Label htmlFor="apply-sms-code">Verification code</Label>
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
            {busy ? "Verifying…" : "Verify & continue"}
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

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {/* Invisible reCAPTCHA host for Firebase Phone Auth. */}
      <div id={RECAPTCHA_CONTAINER_ID} aria-hidden="true" />
    </div>
  );
}

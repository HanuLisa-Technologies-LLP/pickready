"use client";

import * as React from "react";
import Link from "next/link";
import {
  GoogleAuthProvider,
  RecaptchaVerifier,
  createUserWithEmailAndPassword,
  signInWithPhoneNumber,
  signInWithPopup,
  updateProfile,
  type ConfirmationResult,
} from "firebase/auth";
import { useRouter } from "next/navigation";

import { firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  type FirebaseExchangeResult,
} from "@/lib/firebase-session";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";
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

const RECAPTCHA_CONTAINER_ID = "firebase-recaptcha-register";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD = 8;

export function RegisterFlow() {
  const router = useRouter();
  const { setSession } = useAuth();

  const [method, setMethod] = React.useState<Method>("password");
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [confirmation, setConfirmation] =
    React.useState<ConfirmationResult | null>(null);
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

  // Candidate self sign-up only — the backend creates candidates for unknown
  // identities. Anything else means the identity already belongs to a staff
  // account; send them to the login page rather than pretending to register.
  const handleExchange = React.useCallback(
    (result: FirebaseExchangeResult) => {
      if (isContextsResponse(result) || result.user.role !== "candidate") {
        void firebaseAuth.signOut().catch(() => {});
        setError(
          "This account already exists. Please sign in instead."
        );
        return;
      }
      setSession(result.user, result.capabilities ?? []);
      setRedirecting(true);
      router.replace(homePathForRole(result.user.role));
    },
    [router, setSession]
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

  const registerPassword = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }
    if (password.length < MIN_PASSWORD) {
      setError(`Choose a password with at least ${MIN_PASSWORD} characters.`);
      return;
    }
    run(async () => {
      const credential = await createUserWithEmailAndPassword(
        firebaseAuth,
        trimmedEmail,
        password
      );
      await updateProfile(credential.user, { displayName: name.trim() });
      handleExchange(await exchangeFirebaseSession(credential.user));
    });
  };

  const registerGoogle = () =>
    run(async () => {
      const cred = await signInWithPopup(
        firebaseAuth,
        new GoogleAuthProvider()
      );
      handleExchange(await exchangeFirebaseSession(cred.user));
    });

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
      if (name.trim() && !cred.user.displayName) {
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

  if (redirecting) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <span className="text-2xl font-bold tracking-tight text-foreground">
              PickReady
            </span>
            <p className="animate-pulse text-sm text-muted-foreground" role="status">
              Setting up your account…
            </p>
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
          <span className="mb-2 text-2xl font-bold tracking-tight text-foreground">
            PickReady
          </span>
          <CardTitle>Create your candidate account</CardTitle>
          <CardDescription>
            Sign up with Google, an email and password, or your phone number.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={busy}
            onClick={registerGoogle}
          >
            Continue with Google
          </Button>

          <div className="flex items-center gap-3">
            <span className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">or</span>
            <span className="h-px flex-1 bg-border" />
          </div>

          <div className="space-y-1">
            <Label htmlFor="name">Full name</Label>
            <Input
              id="name"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div
            className="grid grid-cols-2 gap-2"
            role="group"
            aria-label="Sign-up method"
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
            <form className="space-y-3" onSubmit={registerPassword}>
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
                  autoComplete="new-password"
                  minLength={MIN_PASSWORD}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  At least {MIN_PASSWORD} characters.
                </p>
              </div>
              <Button className="w-full" disabled={busy}>
                {busy ? "Creating…" : "Create account"}
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
                {busy ? "Verifying…" : "Verify and create account"}
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

          <p className="text-center text-xs text-muted-foreground">
            Already registered?{" "}
            <Link className="underline" href="/login">
              Sign in
            </Link>
          </p>

          {/* Invisible reCAPTCHA host for Firebase Phone Auth. */}
          <div id={RECAPTCHA_CONTAINER_ID} aria-hidden="true" />
        </CardContent>
      </Card>
    </div>
  );
}

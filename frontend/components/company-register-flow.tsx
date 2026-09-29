"use client";

// Register your company (owner spec 2026-09-29, sections 2.2 and 5).
//
// Details and a security check -> a security code from the company mailbox ->
// the first credit pack -> a password -> the company workspace. The SERVER
// decides every step: `GET /company-onboarding/state` says where this browser's
// registration stands (from a short-lived, httpOnly onboarding cookie the
// code step sets), so a reload lands on the right step and nothing here is a
// source of truth. The browser's payment callback proves nothing by itself:
// the server checks Razorpay's signature, and the password step is refused
// until a paid purchase exists.
//
// There is no Google here. The Company Super Admin signs in with an email and
// a password, like every company role.

import * as React from "react";
import Link from "next/link";
import { Eye, EyeOff, Loader2, MailCheck } from "lucide-react";
import { signInWithEmailAndPassword } from "firebase/auth";
import { useRouter } from "next/navigation";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { firebaseAuth } from "@/lib/firebase";
import { friendlyAuthError } from "@/lib/firebase-session";
import { useAuth } from "@/lib/auth-context";
import { openOrderCheckout } from "@/lib/razorpay";
import type {
  AuthSession,
  CreditPack,
  CreditPacksResponse,
  OnboardingState,
  PurchaseCreateResponse,
} from "@/lib/types";
import { AuthLink, AuthShell } from "@/components/auth-shell";
import { CreditPackPicker } from "@/components/billing/credit-pack-picker";
import { Captcha, CaptchaError, type CaptchaHandle } from "@/components/captcha";
import { INDUSTRIES } from "@/components/customer-edit-modal";
import { InlineError, LoadingRows } from "@/components/page-primitives";
import {
  PasswordRules,
  isPasswordValid,
  passwordRules,
} from "@/components/password-rules";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const BASE = "/company-onboarding";

type Details = {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  company_name: string;
  industry: string;
  industry_other: string;
};

const EMPTY_DETAILS: Details = {
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  company_name: "",
  industry: "",
  industry_other: "",
};

/** The server's sentence when it gave one, else `fallback`. */
function sentence(error: unknown, fallback: string): string {
  if (error instanceof CaptchaError) return error.message;
  if (error instanceof ApiError && error.message) return error.message;
  return fallback;
}

const SELECT_CLASS =
  "flex h-10 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm transition-[border-color,box-shadow] duration-150 hover:border-field-hover focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50";

export function CompanyRegisterFlow() {
  const router = useRouter();
  const { setSession } = useAuth();
  const [state, setState] = React.useState<OnboardingState | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [resuming, setResuming] = React.useState(false);
  const [details, setDetails] = React.useState<Details>(EMPTY_DETAILS);
  const [pendingEmail, setPendingEmail] = React.useState("");
  const [code, setCode] = React.useState("");
  const [packs, setPacks] = React.useState<CreditPacksResponse | null>(null);
  const [selectedSlug, setSelectedSlug] = React.useState<string | null>(null);
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [existingSignIn, setExistingSignIn] = React.useState(false);
  const captcha = React.useRef<CaptchaHandle>(null);
  const rules = passwordRules(password);

  const loadState = React.useCallback(async () => {
    setLoadError(null);
    try {
      setState(await apiGet<OnboardingState>(`${BASE}/state`));
    } catch (failure) {
      setLoadError(sentence(failure, "We could not load your registration. Refresh to try again."));
    }
  }, []);

  React.useEffect(() => {
    void loadState();
  }, [loadState]);

  // The pack step reads its prices from the server, for THIS company.
  React.useEffect(() => {
    if (state?.stage !== "choose_pack" || packs) return;
    apiGet<CreditPacksResponse>(`${BASE}/pricing`)
      .then(setPacks)
      .catch((failure) => {
        if (failure instanceof ApiError && failure.status === 401) {
          setState({ stage: "details" });
          setResuming(true);
        }
        setError(sentence(failure, "We could not load the credit packs."));
      });
  }, [state?.stage, packs]);

  const run = (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    void action()
      .catch((failure) => setError(sentence(failure, "That did not go through. Please try again.")))
      .finally(() => setBusy(false));
  };

  const field = (key: keyof Details) => ({
    value: details[key],
    disabled: busy,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setDetails((current) => ({ ...current, [key]: event.target.value })),
  });

  const submitDetails = (event: React.FormEvent) => {
    event.preventDefault();
    const required: Array<keyof Details> = [
      "first_name", "last_name", "email", "phone", "company_name", "industry",
    ];
    if (required.some((key) => !details[key].trim())) {
      setError("Fill in every field to register your company.");
      return;
    }
    if (details.industry === "Other" && !details.industry_other.trim()) {
      setError("Tell us your industry when you choose Other.");
      return;
    }
    run(async () => {
      const proof = await captcha.current!.prove();
      await apiPost(`${BASE}/register`, {
        ...details,
        industry_other: details.industry === "Other" ? details.industry_other : null,
        captcha_proof: proof,
      });
      setPendingEmail(details.email.trim());
      setState({ stage: "verify_email", email: details.email.trim() });
    });
  };

  const submitResume = (event: React.FormEvent) => {
    event.preventDefault();
    if (!pendingEmail.trim()) {
      setError("Enter the email address you registered with.");
      return;
    }
    run(async () => {
      await apiPost(`${BASE}/code/resend`, { email: pendingEmail.trim() });
      setResuming(false);
      setState({ stage: "verify_email", email: pendingEmail.trim() });
    });
  };

  const email = state?.email ?? pendingEmail;

  const submitCode = (event: React.FormEvent) => {
    event.preventDefault();
    if (!code.trim()) {
      setError("Enter the six-digit security code from the email.");
      return;
    }
    run(async () => {
      const next = await apiPost<OnboardingState>(`${BASE}/code/verify`, {
        email,
        code: code.trim(),
      });
      setCode("");
      setState(next);
    });
  };

  const resend = () =>
    run(async () => {
      await apiPost(`${BASE}/code/resend`, { email });
      setNotice("A new security code is on its way. Use the newest email.");
    });

  const buy = (pack: CreditPack) =>
    run(async () => {
      const order = await apiPost<PurchaseCreateResponse>(`${BASE}/purchase`, {
        pack_slug: pack.slug,
      });
      await new Promise<void>((resolve, reject) => {
        void openOrderCheckout({
          keyId: order.razorpay_key_id,
          orderId: order.razorpay_order_id,
          amountInr: order.total_inr,
          name: "Vivekium",
          description: `${order.credits} Intelligence Report credits, first purchase`,
          prefill: { email, name: state?.first_name ?? undefined },
          onSuccess: (payload) => {
            apiPost<OnboardingState>(`${BASE}/purchase/verify`, payload)
              .then((next) => {
                setState(next);
                resolve();
              })
              .catch(reject);
          },
          onDismiss: () => resolve(),
        }).then((opened) => {
          if (!opened) {
            reject(
              new Error(
                "Checkout could not open. Check that your browser is not blocking payment scripts, then retry."
              )
            );
          }
        });
      });
    });

  const finish = (session: AuthSession) => {
    setSession(session.user, session.capabilities ?? []);
    router.replace("/org");
  };

  const submitPassword = (event: React.FormEvent) => {
    event.preventDefault();
    if (existingSignIn) {
      if (!password) {
        setError("Enter your existing password.");
        return;
      }
      run(async () => {
        try {
          const credential = await signInWithEmailAndPassword(firebaseAuth, email, password);
          finish(
            await apiPost<AuthSession>(`${BASE}/activate`, {
              id_token: await credential.user.getIdToken(),
            })
          );
        } catch (failure) {
          if (failure instanceof ApiError) throw failure;
          throw new Error(friendlyAuthError(failure) ?? "That password did not work.");
        }
      });
      return;
    }
    if (!isPasswordValid(rules)) {
      setError("Use at least 8 characters with an uppercase letter, a lowercase letter and a number.");
      return;
    }
    run(async () => {
      try {
        finish(await apiPost<AuthSession>(`${BASE}/activate`, { password }));
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 409) {
          // The address already has a sign-in: finish with it instead.
          setExistingSignIn(true);
          setPassword("");
        }
        throw failure;
      }
    });
  };

  if (!state && !loadError) {
    return (
      <AuthShell title="Register your company">
        <LoadingRows rows={3} label="Loading your registration" />
      </AuthShell>
    );
  }

  if (loadError || !state) {
    return (
      <AuthShell title="Register your company" description={loadError}>
        <Button size="lg" className="w-full" onClick={() => void loadState()}>
          Try again
        </Button>
      </AuthShell>
    );
  }

  const footer = (
    <p>
      Already registered? <AuthLink href="/company/login">Company login</AuthLink>
    </p>
  );

  if (state.stage === "done") {
    return (
      <AuthShell
        title="Your company is ready"
        description="Your registration is finished. Sign in with your email and password."
      >
        <Button asChild size="lg" className="w-full">
          <Link href="/company/login">Go to company login</Link>
        </Button>
      </AuthShell>
    );
  }

  if (state.stage === "details" && resuming) {
    return (
      <AuthShell
        title="Continue your registration"
        description="Enter the email address you registered with and we will send a new security code."
        footer={footer}
      >
        <form className="space-y-4" onSubmit={submitResume}>
          <div className="space-y-1.5">
            <Label htmlFor="resume-email">Company email</Label>
            <Input
              id="resume-email"
              type="email"
              autoComplete="email"
              value={pendingEmail}
              disabled={busy}
              onChange={(event) => setPendingEmail(event.target.value)}
            />
          </div>
          <Button size="lg" className="w-full" disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Send a security code
          </Button>
        </form>
        <p className="text-center text-sm">
          <button type="button" className="font-semibold underline" onClick={() => setResuming(false)}>
            Start a new registration instead
          </button>
        </p>
        {error ? <InlineError>{error}</InlineError> : null}
      </AuthShell>
    );
  }

  if (state.stage === "details") {
    return (
      <AuthShell
        title="Register your company"
        description="Create your company workspace. You will be its Company Super Admin and can invite your team after the first credit purchase."
        footer={footer}
      >
        <form className="space-y-4" onSubmit={submitDetails}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="reg-first">First name</Label>
              <Input id="reg-first" autoComplete="given-name" {...field("first_name")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reg-last">Last name</Label>
              <Input id="reg-last" autoComplete="family-name" {...field("last_name")} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reg-email">Company email</Label>
            <Input id="reg-email" type="email" autoComplete="email" {...field("email")} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reg-phone">Mobile number</Label>
            <Input
              id="reg-phone"
              type="tel"
              autoComplete="tel"
              placeholder="+91 98765 43210"
              {...field("phone")}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reg-company">Company name</Label>
            <Input id="reg-company" autoComplete="organization" {...field("company_name")} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reg-industry">Industry</Label>
            <select id="reg-industry" className={SELECT_CLASS} {...field("industry")}>
              <option value="" disabled>
                Choose an industry
              </option>
              {INDUSTRIES.map((industry) => (
                <option key={industry} value={industry}>
                  {industry}
                </option>
              ))}
            </select>
          </div>
          {details.industry === "Other" ? (
            <div className="space-y-1.5">
              <Label htmlFor="reg-industry-other">Your industry</Label>
              <Input id="reg-industry-other" {...field("industry_other")} />
            </div>
          ) : null}
          <Captcha ref={captcha} purpose="company_register" disabled={busy} idPrefix="reg-captcha" />
          <Button size="lg" className="w-full" disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Send security code
          </Button>
        </form>
        <p className="text-center text-sm">
          Already started?{" "}
          <button
            type="button"
            className="font-semibold underline"
            onClick={() => {
              setError(null);
              setResuming(true);
            }}
          >
            Continue your registration
          </button>
        </p>
        {error ? <InlineError>{error}</InlineError> : null}
      </AuthShell>
    );
  }

  if (state.stage === "verify_email") {
    return (
      <AuthShell
        title="Check your email"
        description="Enter the six-digit security code we sent to your company email. It expires in ten minutes."
        footer={footer}
      >
        <div className="flex items-start gap-3 rounded-xl border border-border bg-brand-100/50 px-4 py-3">
          <MailCheck className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" aria-hidden="true" />
          <p className="min-w-0 truncate text-sm font-semibold">{email}</p>
        </div>
        <form className="space-y-4" onSubmit={submitCode}>
          <div className="space-y-1.5">
            <Label htmlFor="reg-code">Security code</Label>
            <Input
              id="reg-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              disabled={busy}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            />
          </div>
          <Button size="lg" className="w-full" disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Verify email
          </Button>
        </form>
        <p className="text-center text-sm">
          No email?{" "}
          <button type="button" className="font-semibold underline" disabled={busy} onClick={resend}>
            Send a new security code
          </button>
        </p>
        {notice ? <p role="status" className="text-center text-sm">{notice}</p> : null}
        {error ? <InlineError>{error}</InlineError> : null}
      </AuthShell>
    );
  }

  if (state.stage === "choose_pack") {
    return (
      <AuthShell
        className="max-w-4xl"
        title="Choose your first credit pack"
        description={`Your first purchase activates ${state.company_name ?? "your company"}. Credits are one-time purchases; there is no subscription.`}
        footer={footer}
      >
        {packs ? (
          <CreditPackPicker
            packs={packs}
            selectedSlug={selectedSlug}
            onSelect={setSelectedSlug}
            onBuy={buy}
            busy={busy}
            showEnterprise={false}
          />
        ) : error ? null : (
          <LoadingRows rows={2} label="Loading credit packs" />
        )}
        {error ? <InlineError>{error}</InlineError> : null}
      </AuthShell>
    );
  }

  // set_password
  return (
    <AuthShell
      title="Set your password"
      description="Payment confirmed. Set the password you will use to sign in to your company workspace."
      footer={footer}
    >
      <form className="space-y-4" onSubmit={submitPassword}>
        <div className="space-y-1.5">
          <Label htmlFor="reg-login-email">Email address</Label>
          <Input id="reg-login-email" type="email" autoComplete="username" value={email} readOnly aria-readonly="true" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="reg-password">
            {existingSignIn ? "Your existing password" : "Create a password"}
          </Label>
          <div className="relative">
            <Input
              id="reg-password"
              type={showPassword ? "text" : "password"}
              autoComplete={existingSignIn ? "current-password" : "new-password"}
              value={password}
              disabled={busy}
              className="pr-11"
              onChange={(event) => setPassword(event.target.value)}
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
          {existingSignIn ? null : <PasswordRules rules={rules} />}
        </div>
        <Button size="lg" className="w-full" disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          {existingSignIn ? "Sign in and open workspace" : "Set password and open workspace"}
        </Button>
      </form>
      {error ? <InlineError>{error}</InlineError> : null}
    </AuthShell>
  );
}

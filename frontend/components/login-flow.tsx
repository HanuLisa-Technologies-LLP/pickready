"use client";

// Single OTP-only login for ALL roles — Owner, client-org roles and
// candidates (contract rev 2, FR-1.1). Handles:
//  - single-user verify → {user, capabilities} → route by portal
//  - multi-user verify → {contexts, context_token} → "Choose your workspace"
//    step, then POST /auth/select-context
//  - first-client-login dual OTP: after a successful email verify, if the
//    client's phone is not yet verified we run a second OTP round on the
//    registered mobile (FR-1.2).

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { apiPost, ApiError } from "@/lib/api";
import {
  isContextsResponse,
  type AuthContextOption,
  type AuthSession,
  type Capability,
  type OtpRequestResponse,
  type OtpVerifyResponse,
  type User,
} from "@/lib/types";
import { useAuth, homePathForRole } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { OtpInput } from "@/components/otp-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Channel = "email" | "sms";
type Step =
  | "identifier"
  | "otp"
  | "choose-context"
  | "phone-identifier"
  | "phone-otp";

// OTP validity window mirrors OTP_TTL_MINUTES (backend default 5 min) and the
// resend throttle mirrors the backend's 30s RESEND_THROTTLE. These are UX hints
// only — the backend remains the source of truth and rejects a stale/early code.
const OTP_TTL_SECONDS = 5 * 60;
const RESEND_COOLDOWN_SECONDS = 30;
// Belt-and-braces lock so a fast double-click can't fire two OTP requests.
const SUBMIT_LOCK_MS = 2000;

const ROLE_LABELS: Record<string, string> = {
  super_admin: "Owner",
  client: "Client",
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
  candidate: "Candidate",
};

function contextLabel(ctx: AuthContextOption): string {
  const role = ROLE_LABELS[ctx.role] ?? ctx.role.replace(/_/g, " ");
  return ctx.tenant_name ? `${role} — ${ctx.tenant_name}` : role;
}

function portalLabel(portal: string): string {
  if (portal === "admin") return "Owner portal";
  if (portal === "portal") return "Candidate portal";
  return "Client-org portal";
}

/** Accurate "where to look" copy from the channels the backend actually sent. */
function describeChannels(channels: Channel[]): string {
  const hasEmail = channels.includes("email");
  const hasSms = channels.includes("sms");
  if (hasEmail && hasSms) return "Check your email and SMS for the code.";
  if (hasSms) return "Check your SMS for the code.";
  return "Check your email for the code.";
}

/** Map an API failure to safe, user-facing copy — never a raw server error. */
function otpErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 401:
        return "That code isn't correct. Check the digits and try again.";
      case 410:
        return "This code has expired. Request a new one to continue.";
      case 429:
        return "Too many incorrect attempts. Please wait 15 minutes before trying again.";
      case 404:
        return "This sign-in session has expired. Request a new code to continue.";
    }
  }
  return "We couldn't verify that code. Please try again.";
}

export function LoginFlow({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setSession, refresh } = useAuth();
  const { toast } = useToast();

  const [step, setStep] = React.useState<Step>("identifier");
  const [channel, setChannel] = React.useState<Channel>("email");
  // Pre-fill the identifier when arriving from candidate sign-up (?identifier=).
  const [identifier, setIdentifier] = React.useState(
    () => searchParams.get("identifier") ?? ""
  );
  const [phone, setPhone] = React.useState("");
  const [challengeId, setChallengeId] = React.useState("");
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [submitLocked, setSubmitLocked] = React.useState(false);
  const [redirecting, setRedirecting] = React.useState(false);
  const [pendingSession, setPendingSession] =
    React.useState<AuthSession | null>(null);

  // OTP step UX state: when the current code was dispatched (drives the
  // validity + resend countdowns), which channels it went to, and any inline
  // verification error to show in red under the boxes.
  const [sentAt, setSentAt] = React.useState(0);
  const [channelsSent, setChannelsSent] = React.useState<Channel[]>([]);
  const [otpError, setOtpError] = React.useState<string | null>(null);

  // Multi-context ("Choose your workspace") state — rendered ONLY when the
  // identifier matched multiple users (rev 2).
  const [contexts, setContexts] = React.useState<AuthContextOption[]>([]);
  const [contextToken, setContextToken] = React.useState("");

  const finish = React.useCallback(
    async (user: User, capabilities: Capability[]) => {
      // Instant client-side navigation; if the session refresh + route change
      // somehow stalls, the "Loading your workspace…" view below shows instead
      // of a half-rendered form.
      setRedirecting(true);
      setSession(user, capabilities);
      await refresh();
      router.replace(homePathForRole(user.role));
    },
    [setSession, refresh, router]
  );

  /** Common post-auth handling: dual-OTP for first client login, else route. */
  const handleSession = React.useCallback(
    async (session: AuthSession, isPhoneRound: boolean) => {
      const { user } = session;
      if (!isPhoneRound && user.role === "client" && !user.phone_verified) {
        // First client login: dual OTP — validate mobile too (FR-1.2).
        setPendingSession(session);
        setStep("phone-identifier");
        toast({
          title: "Phone verification required",
          description:
            "First login requires validating your registered mobile number as well.",
        });
      } else {
        await finish(user, session.capabilities ?? []);
      }
    },
    [finish, toast]
  );

  const requestOtp = async (id: string, ch: Channel, nextStep: Step) => {
    const resending = step === nextStep;
    setBusy(true);
    setOtpError(null);
    try {
      // ASSUMPTION: with a single login page for every role the client cannot
      // know the audience upfront; `audience` is optional per the contract, so
      // it is omitted and the backend resolves matching users itself.
      const res = await apiPost<OtpRequestResponse>("/auth/otp/request", {
        identifier: id,
        channel: ch,
      });
      setChallengeId(res.challenge_id);
      setCode("");
      // Prefer the backend's report of which channels it actually dispatched
      // to; fall back to the requested channel for older backends (defensive —
      // the field is being added concurrently).
      setChannelsSent(
        res.channels_sent && res.channels_sent.length ? res.channels_sent : [ch]
      );
      setSentAt(Date.now());
      setStep(nextStep);
    } catch (e) {
      const description =
        e instanceof Error ? e.message : "Please try again.";
      if (resending) {
        // Already on the OTP screen — surface the reason inline (e.g. the 30s
        // resend throttle) instead of a toast that competes with the form.
        setOtpError(description);
      } else {
        toast({
          title: "Could not send code",
          description,
          variant: "destructive",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const verifyOtp = async (isPhoneRound: boolean) => {
    setBusy(true);
    setOtpError(null);
    try {
      const res = await apiPost<OtpVerifyResponse>("/auth/otp/verify", {
        challenge_id: challengeId,
        code,
      });
      if (isContextsResponse(res)) {
        // Multiple matching users — no cookies yet; choose a workspace first.
        setContexts(res.contexts);
        setContextToken(res.context_token);
        setStep("choose-context");
      } else {
        await handleSession(res, isPhoneRound);
      }
    } catch (e) {
      setOtpError(otpErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const selectContext = async (ctx: AuthContextOption) => {
    setBusy(true);
    try {
      const res = await apiPost<AuthSession>("/auth/select-context", {
        context_token: contextToken,
        user_id: ctx.user_id,
      });
      await handleSession(res, false);
    } catch (e) {
      toast({
        title: "Could not open workspace",
        description: e instanceof Error ? e.message : "Please sign in again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  /** Guarded identifier submit — locks the button for ~2s to stop double-sends. */
  const submitIdentifier = (id: string, ch: Channel, nextStep: Step) => {
    if (busy || submitLocked || !id.trim()) return;
    setSubmitLocked(true);
    window.setTimeout(() => setSubmitLocked(false), SUBMIT_LOCK_MS);
    void requestOtp(id.trim(), ch, nextStep);
  };

  const onCodeChange = (v: string) => {
    setCode(v);
    if (otpError) setOtpError(null);
  };

  const headerTitle = redirecting
    ? "Signing you in"
    : step === "choose-context"
      ? "Choose your workspace"
      : title;
  const headerDescription = redirecting
    ? "Taking you to your workspace."
    : step === "choose-context"
      ? "Your identifier matches more than one account. Pick where to sign in."
      : description;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="space-y-1 text-center">
          <p className="text-sm font-semibold tracking-tight text-muted-foreground">
            PickReady
          </p>
          <CardTitle className="text-xl">{headerTitle}</CardTitle>
          <CardDescription>{headerDescription}</CardDescription>
        </CardHeader>
        <CardContent>
          {redirecting ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              Loading your workspace…
            </p>
          ) : null}

          {!redirecting && step === "identifier" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                submitIdentifier(identifier, channel, "otp");
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="channel">Sign in with</Label>
                <Select
                  value={channel}
                  onValueChange={(v) => setChannel(v as Channel)}
                >
                  <SelectTrigger id="channel">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="email">Email OTP</SelectItem>
                    <SelectItem value="sms">SMS OTP</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="identifier">
                  {channel === "email" ? "Email address" : "Mobile number"}
                </Label>
                <Input
                  id="identifier"
                  type={channel === "email" ? "email" : "tel"}
                  placeholder={
                    channel === "email" ? "you@example.com" : "+91 98765 43210"
                  }
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={busy || submitLocked}
              >
                {busy ? "Sending…" : "Send OTP"}
              </Button>
              <p className="text-center text-xs text-muted-foreground">
                No passwords — PickReady uses one-time codes only.
              </p>
              <p className="text-center text-xs text-muted-foreground">
                New candidate?{" "}
                <Link
                  href="/register"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  Create an account
                </Link>
              </p>
            </form>
          ) : null}

          {!redirecting && step === "otp" ? (
            <OtpStep
              destination={identifier}
              channelsSent={channelsSent}
              requestedChannel={channel}
              sentAt={sentAt}
              busy={busy}
              code={code}
              error={otpError}
              onCodeChange={onCodeChange}
              onVerify={() => void verifyOtp(false)}
              onResend={() => void requestOtp(identifier, channel, "otp")}
              verifyLabel="Verify & sign in"
            >
              <button
                type="button"
                className="w-full text-center text-xs text-muted-foreground underline-offset-2 hover:underline"
                onClick={() => {
                  setOtpError(null);
                  setCode("");
                  setStep("identifier");
                }}
              >
                Change identifier
              </button>
            </OtpStep>
          ) : null}

          {!redirecting && step === "choose-context" ? (
            <div className="space-y-2">
              {contexts.map((ctx) => (
                <button
                  key={ctx.user_id}
                  type="button"
                  disabled={busy}
                  className="flex w-full items-center justify-between rounded-md border p-3 text-left text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
                  onClick={() => void selectContext(ctx)}
                >
                  <span>
                    <span className="block font-medium">
                      {contextLabel(ctx)}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {portalLabel(ctx.portal)}
                    </span>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {busy ? "…" : "Continue →"}
                  </span>
                </button>
              ))}
              <button
                type="button"
                className="w-full text-center text-xs text-muted-foreground underline-offset-2 hover:underline"
                onClick={() => {
                  setContexts([]);
                  setContextToken("");
                  setStep("identifier");
                }}
              >
                Sign in with a different identifier
              </button>
            </div>
          ) : null}

          {!redirecting && step === "phone-identifier" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                submitIdentifier(phone, "sms", "phone-otp");
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="phone">Registered mobile number</Label>
                <Input
                  id="phone"
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  required
                  autoFocus
                />
                <p className="text-xs text-muted-foreground">
                  Your first login validates both email and mobile (dual OTP).
                </p>
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={busy || submitLocked}
              >
                {busy ? "Sending…" : "Send SMS OTP"}
              </Button>
            </form>
          ) : null}

          {!redirecting && step === "phone-otp" ? (
            <OtpStep
              destination={phone}
              channelsSent={channelsSent}
              requestedChannel="sms"
              sentAt={sentAt}
              busy={busy}
              code={code}
              error={otpError}
              onCodeChange={onCodeChange}
              onVerify={() => void verifyOtp(true)}
              onResend={() => void requestOtp(phone, "sms", "phone-otp")}
              verifyLabel="Verify phone & continue"
            >
              {pendingSession ? (
                <button
                  type="button"
                  className="w-full text-center text-xs text-muted-foreground underline-offset-2 hover:underline"
                  onClick={() =>
                    void finish(
                      pendingSession.user,
                      pendingSession.capabilities ?? []
                    )
                  }
                >
                  Skip for now (phone stays unverified)
                </button>
              ) : null}
            </OtpStep>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Shared OTP entry screen: 6-box input, a live validity countdown, an accurate
 * "check your email/SMS" line, a throttled resend, and inline error copy.
 * Used for both the primary email round and the first-login phone round.
 */
function OtpStep({
  destination,
  channelsSent,
  requestedChannel,
  sentAt,
  busy,
  code,
  error,
  onCodeChange,
  onVerify,
  onResend,
  verifyLabel,
  children,
}: {
  destination: string;
  channelsSent: Channel[];
  requestedChannel: Channel;
  sentAt: number;
  busy: boolean;
  code: string;
  error: string | null;
  onCodeChange: (v: string) => void;
  onVerify: () => void;
  onResend: () => void;
  verifyLabel: string;
  children?: React.ReactNode;
}) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const elapsed = Math.max(0, Math.floor((now - sentAt) / 1000));
  const remaining = Math.max(0, OTP_TTL_SECONDS - elapsed);
  const expired = sentAt > 0 && remaining === 0;
  const resendIn = Math.max(0, RESEND_COOLDOWN_SECONDS - elapsed);

  const channels = channelsSent.length ? channelsSent : [requestedChannel];
  const mmss = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!expired && !busy && code.length === 6) onVerify();
      }}
    >
      <div className="space-y-1">
        <Label htmlFor="otp-code">
          Enter the 6-digit code sent to {destination}
        </Label>
        <p className="text-xs text-muted-foreground">
          {describeChannels(channels)}
        </p>
      </div>

      <OtpInput
        value={code}
        onChange={onCodeChange}
        disabled={busy || expired}
        invalid={Boolean(error) || expired}
        autoFocus
      />

      {expired ? (
        <p className="text-xs font-medium text-red-600 dark:text-red-400">
          Your code has expired. Request a new one below.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          Expires in {mmss}
        </p>
      )}

      {error ? (
        <p
          role="alert"
          className="text-sm font-medium text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      ) : null}

      <Button
        type="submit"
        className="w-full"
        disabled={busy || expired || code.length !== 6}
      >
        {busy ? "Verifying…" : verifyLabel}
      </Button>

      <div className="flex items-center justify-center gap-1 text-xs">
        <span className="text-muted-foreground">Didn&apos;t receive it?</span>
        <button
          type="button"
          className="font-medium underline-offset-2 hover:underline disabled:no-underline disabled:opacity-60"
          disabled={busy || resendIn > 0}
          onClick={onResend}
        >
          {resendIn > 0 ? `Resend in ${resendIn}s` : "Resend code"}
        </button>
      </div>

      {children}
    </form>
  );
}

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
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";
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

export function LoginFlow({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  const router = useRouter();
  const { setSession, refresh } = useAuth();
  const { toast } = useToast();

  const [step, setStep] = React.useState<Step>("identifier");
  const [channel, setChannel] = React.useState<Channel>("email");
  const [identifier, setIdentifier] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [challengeId, setChallengeId] = React.useState("");
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [pendingSession, setPendingSession] =
    React.useState<AuthSession | null>(null);

  // Multi-context ("Choose your workspace") state — rendered ONLY when the
  // identifier matched multiple users (rev 2).
  const [contexts, setContexts] = React.useState<AuthContextOption[]>([]);
  const [contextToken, setContextToken] = React.useState("");

  const finish = React.useCallback(
    async (user: User, capabilities: Capability[]) => {
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
    setBusy(true);
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
      setStep(nextStep);
      toast({
        title: "OTP sent",
        description: `A one-time code was sent via ${ch === "email" ? "email" : "SMS"}.`,
      });
    } catch (e) {
      toast({
        title: "Could not send OTP",
        description: e instanceof Error ? e.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const verifyOtp = async (isPhoneRound: boolean) => {
    setBusy(true);
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
      toast({
        title: "Verification failed",
        description:
          e instanceof Error ? e.message : "Invalid or expired code.",
        variant: "destructive",
      });
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">
            {step === "choose-context" ? "Choose your workspace" : title}
          </CardTitle>
          <CardDescription>
            {step === "choose-context"
              ? "Your identifier matches more than one account. Pick where to sign in."
              : description}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {step === "identifier" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (identifier.trim()) {
                  void requestOtp(identifier.trim(), channel, "otp");
                }
              }}
            >
              <div className="space-y-2">
                <Label>Sign in with</Label>
                <Select
                  value={channel}
                  onValueChange={(v) => setChannel(v as Channel)}
                >
                  <SelectTrigger>
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
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Sending…" : "Send OTP"}
              </Button>
              <p className="text-center text-xs text-muted-foreground">
                No passwords — PickReady uses one-time codes only.
              </p>
            </form>
          ) : null}

          {step === "otp" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (code.length === 6) void verifyOtp(false);
              }}
            >
              <div className="space-y-2">
                <Label>Enter the 6-digit code sent to {identifier}</Label>
                <OtpInput value={code} onChange={setCode} disabled={busy} />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={busy || code.length !== 6}
              >
                {busy ? "Verifying…" : "Verify & sign in"}
              </Button>
              <div className="flex justify-between text-xs">
                <button
                  type="button"
                  className="text-muted-foreground underline-offset-2 hover:underline"
                  onClick={() => setStep("identifier")}
                >
                  Change identifier
                </button>
                <button
                  type="button"
                  className="text-muted-foreground underline-offset-2 hover:underline"
                  disabled={busy}
                  onClick={() => void requestOtp(identifier, channel, "otp")}
                >
                  Resend code
                </button>
              </div>
            </form>
          ) : null}

          {step === "choose-context" ? (
            <div className="space-y-2">
              {contexts.map((ctx) => (
                <button
                  key={ctx.user_id}
                  type="button"
                  disabled={busy}
                  className="flex w-full items-center justify-between rounded-md border p-3 text-left text-sm transition-colors hover:bg-accent disabled:opacity-50"
                  onClick={() => void selectContext(ctx)}
                >
                  <span>
                    <span className="block font-medium">
                      {contextLabel(ctx)}
                    </span>
                    <span className="block text-xs text-muted-foreground capitalize">
                      {ctx.portal === "admin"
                        ? "Owner portal"
                        : ctx.portal === "portal"
                          ? "Candidate portal"
                          : "Client-org portal"}
                    </span>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {busy ? "…" : "Open →"}
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

          {step === "phone-identifier" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (phone.trim()) {
                  void requestOtp(phone.trim(), "sms", "phone-otp");
                }
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
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Sending…" : "Send SMS OTP"}
              </Button>
            </form>
          ) : null}

          {step === "phone-otp" ? (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (code.length === 6) void verifyOtp(true);
              }}
            >
              <div className="space-y-2">
                <Label>Enter the 6-digit code sent to {phone}</Label>
                <OtpInput value={code} onChange={setCode} disabled={busy} />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={busy || code.length !== 6}
              >
                {busy ? "Verifying…" : "Verify phone & continue"}
              </Button>
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
            </form>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

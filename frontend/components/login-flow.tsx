"use client";

// OTP-only login (FR-1.1). Handles the first-client-login dual OTP:
// after a successful email verify, if the client's phone is not yet verified
// we immediately run a second OTP round on the registered mobile (FR-1.2).

import * as React from "react";
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";
import type { OtpRequestResponse, OtpVerifyResponse, User } from "@/lib/types";
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
type Step = "identifier" | "otp" | "phone-identifier" | "phone-otp";

export function LoginFlow({
  audience,
  title,
  description,
}: {
  audience: "internal" | "candidate";
  title: string;
  description: string;
}) {
  const router = useRouter();
  const { setUser, refresh } = useAuth();
  const { toast } = useToast();

  const [step, setStep] = React.useState<Step>("identifier");
  const [channel, setChannel] = React.useState<Channel>("email");
  const [identifier, setIdentifier] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [challengeId, setChallengeId] = React.useState("");
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [pendingUser, setPendingUser] = React.useState<User | null>(null);

  const finish = React.useCallback(
    async (user: User) => {
      setUser(user);
      await refresh();
      router.replace(homePathForRole(user.role));
    },
    [setUser, refresh, router]
  );

  const requestOtp = async (id: string, ch: Channel, nextStep: Step) => {
    setBusy(true);
    try {
      const res = await apiPost<OtpRequestResponse>("/auth/otp/request", {
        identifier: id,
        channel: ch,
        audience,
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
      const user = res.user;
      if (
        !isPhoneRound &&
        user.role === "client" &&
        !user.phone_verified
      ) {
        // First client login: dual OTP — validate mobile too (FR-1.2).
        setPendingUser(user);
        setStep("phone-identifier");
        toast({
          title: "Phone verification required",
          description:
            "First login requires validating your registered mobile number as well.",
        });
      } else {
        await finish(user);
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
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
              {pendingUser ? (
                <button
                  type="button"
                  className="w-full text-center text-xs text-muted-foreground underline-offset-2 hover:underline"
                  onClick={() => void finish(pendingUser)}
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

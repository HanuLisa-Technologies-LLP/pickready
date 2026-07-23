"use client";

// Candidate self sign-up (FR-9.1 — register first, log in later). OTP-only:
// we collect name/email/phone, create the account, then send the candidate to
// the unified login where an OTP proves email ownership. No password anywhere.

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2 } from "lucide-react";

import { apiPost } from "@/lib/api";
import type { CandidateRegisterResponse } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
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

export function RegisterFlow() {
  const router = useRouter();
  const { toast } = useToast();

  const [fullName, setFullName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fullName.trim() || !email.trim()) return;
    setBusy(true);
    try {
      await apiPost<CandidateRegisterResponse>("/auth/register-candidate", {
        full_name: fullName.trim(),
        email: email.trim(),
        phone: phone.trim() || undefined,
      });
      setDone(true);
    } catch (err) {
      toast({
        title: "Could not create account",
        description:
          err instanceof Error ? err.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const goToLogin = () => {
    // Pre-fill the identifier on the login page for convenience.
    router.push(`/login?identifier=${encodeURIComponent(email.trim())}`);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">
            {done ? "Account created" : "Create your candidate account"}
          </CardTitle>
          <CardDescription>
            {done
              ? "You can now sign in with a one-time code."
              : "Register once, then sign in any time with a one-time code — no passwords."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {done ? (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded-md border p-3 text-sm">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
                <p>
                  We&apos;ve registered{" "}
                  <span className="font-medium">{email}</span>. Sign in from the
                  login page — we&apos;ll send a one-time code to verify it&apos;s
                  you.
                </p>
              </div>
              <Button className="w-full" onClick={goToLogin}>
                Continue to sign in
              </Button>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-2">
                <Label htmlFor="full_name">Full name</Label>
                <Input
                  id="full_name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Your name"
                  required
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="phone">Mobile number (optional)</Label>
                <Input
                  id="phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+91 98765 43210"
                />
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={busy || !fullName.trim() || !email.trim()}
              >
                {busy ? "Creating…" : "Create account"}
              </Button>
              <p className="text-center text-xs text-muted-foreground">
                Already registered?{" "}
                <Link
                  href="/login"
                  className="underline underline-offset-2 hover:text-foreground"
                >
                  Sign in
                </Link>
              </p>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import * as React from "react";
import { Check, Eye, EyeOff, Loader2, MailCheck } from "lucide-react";
import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signInWithPopup,
  updateProfile,
} from "firebase/auth";
import { useRouter } from "next/navigation";

import { cn } from "@/lib/utils";
import { apiGet, apiPost } from "@/lib/api";
import { createCandidateGoogleProvider, firebaseAuth } from "@/lib/firebase";
import {
  exchangeFirebaseSession,
  friendlyAuthError,
  isContextsResponse,
  selectContext,
} from "@/lib/firebase-session";
import { useAuth } from "@/lib/auth-context";
import type { AuthSession, StaffRole } from "@/lib/types";
import { AuthDivider, AuthShell } from "@/components/auth-shell";
import { GoogleMark } from "@/components/google-mark";
import { InlineError, LoadingRows } from "@/components/page-primitives";
import {
  PasswordRules,
  isPasswordValid,
  passwordRules,
} from "@/components/password-rules";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type InviteInfo = {
  email: string;
  full_name?: string | null;
  role: StaffRole;
  company_name: string;
  invited_by_name?: string | null;
  expires_at: string;
  status: "pending";
};

const ROLE_LABELS: Record<StaffRole, string> = {
  recruitment_manager: "Recruitment Manager",
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
};

export function JoinFlow({ token }: { token: string }) {
  const router = useRouter();
  const { setSession } = useAuth();
  const [invite, setInvite] = React.useState<InviteInfo | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<"create" | "signin">("create");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [accepted, setAccepted] = React.useState(false);
  const rules = passwordRules(password);
  const passwordValid = isPasswordValid(rules);

  React.useEffect(() => {
    if (!token) {
      setLoadError("This invitation link is incomplete.");
      setLoading(false);
      return;
    }
    apiGet<InviteInfo>(`/companies/invites/${encodeURIComponent(token)}`)
      .then((result) => {
        setInvite(result);
        setName(result.full_name ?? "");
      })
      .catch((requestError) =>
        setLoadError(
          requestError instanceof Error
            ? requestError.message
            : "This invitation is invalid or no longer available."
        )
      )
      .finally(() => setLoading(false));
  }, [token]);

  const complete = React.useCallback(
    async (session: AuthSession) => {
      if (!invite) return;
      await apiPost(`/companies/invites/${encodeURIComponent(token)}/accept`);
      setSession(session.user, session.capabilities ?? []);
      setAccepted(true);
    },
    [invite, setSession, token]
  );

  const exchangeAndAccept = React.useCallback(
    async (firebaseUser: typeof firebaseAuth.currentUser) => {
      if (!firebaseUser || !invite) return;
      const signedInEmail = firebaseUser.email?.trim().toLowerCase();
      if (signedInEmail !== invite.email.trim().toLowerCase()) {
        await firebaseAuth.signOut();
        throw new Error(
          `Use ${invite.email}; the selected account does not match this invitation.`
        );
      }
      const result = await exchangeFirebaseSession(firebaseUser);
      if (!isContextsResponse(result)) {
        await complete(result);
        return;
      }
      const target = result.contexts.find(
        (context) =>
          context.tenant_name === invite.company_name &&
          context.role === invite.role
      );
      if (!target) {
        throw new Error(
          "Your account was verified, but this company workspace was not available. Ask the company admin to resend the invitation."
        );
      }
      await complete(
        await selectContext(result.context_token, target.user_id)
      );
    },
    [complete, invite]
  );

  const run = (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    void action()
      .catch((authError) => {
        const direct =
          authError instanceof Error &&
          (authError.message.startsWith("Use ") ||
            authError.message.startsWith("Your account"))
            ? authError.message
            : friendlyAuthError(authError);
        if (direct) setError(direct);
      })
      .finally(() => setBusy(false));
  };

  const usePassword = (event: React.FormEvent) => {
    event.preventDefault();
    if (!invite) return;
    if (mode === "create" && (!name.trim() || !passwordValid)) {
      setError(
        "Enter your name and use at least 8 characters with uppercase, lowercase, and a number."
      );
      return;
    }
    run(async () => {
      const credential =
        mode === "create"
          ? await createUserWithEmailAndPassword(
              firebaseAuth,
              invite.email,
              password
            )
          : await signInWithEmailAndPassword(
              firebaseAuth,
              invite.email,
              password
            );
      if (mode === "create" && name.trim()) {
        await updateProfile(credential.user, { displayName: name.trim() });
      }
      await exchangeAndAccept(credential.user);
    });
  };

  const useGoogle = () =>
    run(async () => {
      const credential = await signInWithPopup(
        firebaseAuth,
        createCandidateGoogleProvider()
      );
      await exchangeAndAccept(credential.user);
    });

  if (loading) {
    return (
      <AuthShell title="Checking your invitation">
        <LoadingRows rows={3} label="Checking invitation" />
      </AuthShell>
    );
  }

  if (loadError || !invite) {
    return (
      <AuthShell
        title="Invitation unavailable"
        description={
          loadError ?? "Ask your company admin for a fresh invitation."
        }
      >
        <Button asChild size="lg" className="w-full">
          <a href="/login">Go to sign in</a>
        </Button>
      </AuthShell>
    );
  }

  if (accepted) {
    return (
      <AuthShell
        title={`You joined ${invite.company_name}`}
        description={`Your ${ROLE_LABELS[invite.role]} workspace is ready.`}
      >
        <div className="flex justify-center">
          <span className="grid h-14 w-14 place-items-center rounded-2xl bg-rating-1-bg text-rating-1">
            <Check className="h-7 w-7" strokeWidth={2.5} aria-hidden="true" />
          </span>
        </div>
        <Button size="lg" className="w-full" onClick={() => router.replace("/org")}>
          Open company workspace
        </Button>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={`Join ${invite.company_name}`}
      description={
        <>
          {invite.invited_by_name
            ? `${invite.invited_by_name} invited you`
            : "You were invited"}{" "}
          as {ROLE_LABELS[invite.role]}.
        </>
      }
    >
      <div className="flex items-start gap-3 rounded-xl border border-border bg-brand-100/50 px-4 py-3">
        <MailCheck
          className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{invite.email}</p>
          <p className="mt-0.5 text-xs">
            Expires {new Date(invite.expires_at).toLocaleDateString()}
          </p>
        </div>
      </div>

      <Button
        type="button"
        variant="outline"
        size="lg"
        className="w-full"
        disabled={busy}
        onClick={useGoogle}
      >
        <GoogleMark />
        Continue with Google
      </Button>

      <AuthDivider />

      <div
        role="tablist"
        aria-label="How to join"
        className="grid grid-cols-2 gap-1 rounded-xl border border-border bg-secondary p-1"
      >
        {(["create", "signin"] as const).map((value) => (
          <button
            key={value}
            role="tab"
            type="button"
            aria-selected={mode === value}
            onClick={() => {
              setMode(value);
              setError(null);
            }}
            className={cn(
              "rounded-lg px-3 py-2 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              mode === value
                ? "bg-surface font-semibold shadow-card"
                : "font-medium hover:bg-brand-100/60"
            )}
          >
            {value === "create" ? "Create account" : "Sign in"}
          </button>
        ))}
      </div>

      <form className="space-y-4" onSubmit={usePassword}>
        {mode === "create" ? (
          <div className="space-y-1.5">
            <Label htmlFor="join-name">Full name</Label>
            <Input
              id="join-name"
              autoComplete="name"
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
        ) : null}
        <div className="space-y-1.5">
          <Label htmlFor="join-password">Password</Label>
          <div className="relative">
            <Input
              id="join-password"
              type={showPassword ? "text" : "password"}
              autoComplete={
                mode === "create" ? "new-password" : "current-password"
              }
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
          {mode === "create" ? <PasswordRules rules={rules} /> : null}
        </div>
        <Button
          size="lg"
          className="w-full"
          disabled={busy || (mode === "create" && !passwordValid)}
        >
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Joining
            </>
          ) : mode === "create" ? (
            "Create account and join"
          ) : (
            "Sign in and join"
          )}
        </Button>
      </form>

      {error ? <InlineError>{error}</InlineError> : null}
    </AuthShell>
  );
}

"use client";

// Password change for accounts that actually HAVE a password.
//
// Firebase owns credentials and recovery (claude.md rule 2), this component
// therefore changes the credential through the Firebase SDK. The app then
// revokes every server-side session for this user. No password is stored here.
//
// Visibility rule (client decision, 2026-07-27): the card renders ONLY when the
// signed-in Firebase user carries the "password" provider. A Google-only
// account has no password to change, so offering the form would be a dead end.

import * as React from "react";
import { Eye, EyeOff } from "lucide-react";
import {
  EmailAuthProvider,
  reauthenticateWithCredential,
  signInWithEmailAndPassword,
  updatePassword,
} from "firebase/auth";

import { firebaseAuth } from "@/lib/firebase";
import { apiPost } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const MIN_LENGTH = 8;

/** Firebase's own minimum is 6; 8 is the product floor. */
export function validateNewPassword(next: string, confirm: string): string | null {
  if (next.length < MIN_LENGTH) {
    return `Choose a password with at least ${MIN_LENGTH} characters.`;
  }
  if (next !== confirm) return "The two passwords don't match.";
  return null;
}

function passwordChangeError(error: unknown): string {
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String((error as { code: unknown }).code)
      : "";
  switch (code) {
    case "auth/invalid-credential":
    case "auth/wrong-password":
      return "That current password is incorrect.";
    case "auth/weak-password":
      return `Choose a password with at least ${MIN_LENGTH} characters.`;
    case "auth/too-many-requests":
      return "Too many attempts. Please wait a few minutes and try again.";
    case "auth/requires-recent-login":
      return "For your security, sign out and sign in again before changing your password.";
    case "auth/network-request-failed":
      return "Network error. Check your connection and try again.";
    default:
      return "We couldn't change your password. Please try again.";
  }
}

function PasswordInput({
  id,
  value,
  disabled,
  autoComplete,
  onChange,
}: {
  id: string;
  value: string;
  disabled?: boolean;
  autoComplete: string;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = React.useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        value={value}
        disabled={disabled}
        className="pr-10"
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? "Hide password" : "Show password"}
      >
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

export function ChangePasswordCard() {
  const { toast } = useToast();
  const { user } = useAuth();
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [needsRevocation, setNeedsRevocation] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  if (!user?.password_enabled) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (needsRevocation) {
      setSaving(true);
      try {
        const account = firebaseAuth.currentUser;
        if (!account) throw new Error("Firebase identity is no longer available");
        await apiPost("/auth/password-changed", {
          id_token: await account.getIdToken(true),
        });
        await firebaseAuth.signOut();
        window.location.assign("/login");
      } catch {
        setError("Password changed, but session revocation could not be confirmed. Retry to finish signing out everywhere.");
      } finally {
        setSaving(false);
      }
      return;
    }
    const invalid = validateNewPassword(next, confirm);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (!current) {
      setError("Enter your current password.");
      return;
    }
    if (!user.email) {
      setError("This account has no email address to re-authenticate with.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Firebase identity is in memory only. In a newly opened tab, sign in
      // with the current password; in the original tab, reauthenticate it.
      let account = firebaseAuth.currentUser;
      if (!account || account.email?.toLowerCase() !== user.email.toLowerCase()) {
        account = (await signInWithEmailAndPassword(firebaseAuth, user.email, current)).user;
      } else {
        await reauthenticateWithCredential(
          account, EmailAuthProvider.credential(user.email, current)
        );
      }
      await updatePassword(account, next);
      setNeedsRevocation(true);
      try {
        await apiPost("/auth/password-changed", {
          id_token: await account.getIdToken(true),
        });
      } catch {
        setError("Password changed, but session revocation could not be confirmed. Retry to finish signing out everywhere.");
        return;
      }
      setCurrent("");
      setNext("");
      setConfirm("");
      await firebaseAuth.signOut();
      toast({ title: "Password changed. Sign in again." });
      window.location.assign("/login");
    } catch (changeError) {
      const message = passwordChangeError(changeError);
      setError(message);
      toast({
        title: "Could not change your password",
        description: message,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>
          Change the password you use to sign in.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit} noValidate>
          <FormField label="Current password" htmlFor="password-current" required>
            <PasswordInput
              id="password-current"
              value={current}
              disabled={saving || needsRevocation}
              autoComplete="current-password"
              onChange={setCurrent}
            />
          </FormField>
          <FormField
            label="New password"
            htmlFor="password-new"
            required
            hint={`At least ${MIN_LENGTH} characters.`}
          >
            <PasswordInput
              id="password-new"
              value={next}
              disabled={saving || needsRevocation}
              autoComplete="new-password"
              onChange={setNext}
            />
          </FormField>
          <FormField label="Confirm new password" htmlFor="password-confirm" required>
            <PasswordInput
              id="password-confirm"
              value={confirm}
              disabled={saving || needsRevocation}
              autoComplete="new-password"
              onChange={setConfirm}
            />
          </FormField>
          {error ? (
            <p role="alert" className="text-sm font-medium text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" disabled={saving || (!needsRevocation && (!current || !next || !confirm))}>
            {saving ? "Working" : needsRevocation ? "Finish signing out everywhere" : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

"use client";

// Password change for accounts that actually HAVE a password.
//
// Firebase owns credentials and recovery (claude.md rule 2), this component
// therefore talks to the Firebase client SDK directly and never to a ReadyPick
// endpoint. There is no password column in our database and this does not
// create one.
//
// Visibility rule (client decision, 2026-07-27): the card renders ONLY when the
// signed-in Firebase user carries the "password" provider. A Google-only
// account has no password to change, so offering the form would be a dead end.

import * as React from "react";
import { Eye, EyeOff } from "lucide-react";
import {
  EmailAuthProvider,
  reauthenticateWithCredential,
  updatePassword,
  type User as FirebaseUser,
} from "firebase/auth";

import { firebaseAuth } from "@/lib/firebase";
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
  // `null` = still resolving the Firebase user; `false` = no password provider.
  const [account, setAccount] = React.useState<FirebaseUser | null | false>(null);
  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    // onAuthStateChanged rather than a one-shot read: on a cold load the SDK
    // restores the session asynchronously, so currentUser is briefly null.
    return firebaseAuth.onAuthStateChanged((user) => {
      const hasPassword = Boolean(
        user?.providerData.some((provider) => provider.providerId === "password")
      );
      setAccount(hasPassword && user ? user : false);
    });
  }, []);

  if (account === null || account === false) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const invalid = validateNewPassword(next, confirm);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (!current) {
      setError("Enter your current password.");
      return;
    }
    if (!account.email) {
      setError("This account has no email address to re-authenticate with.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Firebase requires a recent login before a password change;
      // re-authenticating with the current password is that proof.
      await reauthenticateWithCredential(
        account,
        EmailAuthProvider.credential(account.email, current)
      );
      await updatePassword(account, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast({ title: "Password changed" });
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
              disabled={saving}
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
              disabled={saving}
              autoComplete="new-password"
              onChange={setNext}
            />
          </FormField>
          <FormField label="Confirm new password" htmlFor="password-confirm" required>
            <PasswordInput
              id="password-confirm"
              value={confirm}
              disabled={saving}
              autoComplete="new-password"
              onChange={setConfirm}
            />
          </FormField>
          {error ? (
            <p role="alert" className="text-sm font-medium text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" disabled={saving || !current || !next || !confirm}>
            {saving ? "Changing" : "Change password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

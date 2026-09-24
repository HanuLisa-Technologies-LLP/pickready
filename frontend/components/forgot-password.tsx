"use client";

// "Forgot password?" for an email and password account.
//
// FIREBASE OWNS RECOVERY, SO THIS IS ONE CALL. The product stores no password
// and builds no reset flow of its own (claude.md section 3 rule 2): this sends
// Firebase's own reset email, and the link in it lands on Firebase's own page.
// There is no token, no route and no table on our side.
//
// THE ANSWER IS THE SAME WHETHER OR NOT THE ACCOUNT EXISTS. Saying "no account
// uses that address" would turn the sign-in screen into a way to test which
// addresses are registered. So a missing account reads exactly like a sent
// email; only an answer that tells the person something they can act on (a
// malformed address, too many attempts, no network) differs.

import * as React from "react";
import { sendPasswordResetEmail } from "firebase/auth";
import { Loader2, MailCheck } from "lucide-react";

import { firebaseAuth } from "@/lib/firebase";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** The one sentence a person reads after asking, whatever the account state. */
export const RESET_SENT_MESSAGE =
  "If an account uses this address, we have emailed it a link to reset the password. Check your inbox and your spam folder.";

export type ResetOutcome = { sent: true } | { sent: false; message: string };

/**
 * Ask Firebase to email a reset link.
 *
 * `auth/user-not-found` is deliberately an ordinary success (see the header).
 * Every other failure is said, never swallowed: a reset that silently did
 * nothing would leave somebody waiting for an email that is not coming.
 */
export async function requestPasswordReset(email: string): Promise<ResetOutcome> {
  const address = email.trim();
  if (!EMAIL_RE.test(address)) {
    return { sent: false, message: "Enter the email address you sign in with." };
  }
  try {
    await sendPasswordResetEmail(firebaseAuth, address);
    return { sent: true };
  } catch (failure) {
    const code =
      typeof failure === "object" && failure !== null && "code" in failure
        ? String((failure as { code: unknown }).code)
        : "";
    switch (code) {
      case "auth/user-not-found":
        return { sent: true };
      case "auth/invalid-email":
      case "auth/missing-email":
        return { sent: false, message: "Enter the email address you sign in with." };
      case "auth/too-many-requests":
        return {
          sent: false,
          message: "Too many attempts. Please wait a few minutes and try again.",
        };
      case "auth/network-request-failed":
        return {
          sent: false,
          message: "We could not reach the sign-in service. Check your connection and try again.",
        };
      default:
        return {
          sent: false,
          message: "We could not send the reset email right now. Please try again.",
        };
    }
  }
}

/**
 * An inline "Forgot password?" control. Collapsed it is one link-styled
 * button; opened it asks for the address (prefilled from the sign-in form)
 * and states the outcome in place, so nobody is sent to another page.
 */
export function ForgotPassword({
  initialEmail = "",
  idPrefix,
}: {
  initialEmail?: string;
  /** Distinguishes the field when two sign-in forms share a page. */
  idPrefix: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [email, setEmail] = React.useState(initialEmail);
  const [busy, setBusy] = React.useState(false);
  const [sent, setSent] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const fieldId = `${idPrefix}-reset-email`;

  if (!open) {
    return (
      <button
        type="button"
        className="text-sm font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => {
          setEmail(initialEmail);
          setSent(false);
          setError(null);
          setOpen(true);
        }}
      >
        Forgot password?
      </button>
    );
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    const outcome = await requestPasswordReset(email);
    setBusy(false);
    if (outcome.sent) {
      setSent(true);
    } else {
      setError(outcome.message);
    }
  };

  return (
    <div className="space-y-3 rounded-xl border border-border p-4">
      {sent ? (
        <p role="status" className="flex items-start gap-2 text-sm">
          <MailCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {RESET_SENT_MESSAGE}
        </p>
      ) : (
        // A plain group, not a nested <form>: this renders inside the sign-in
        // form, and a form inside a form is invalid HTML whose submit button
        // submits the outer one.
        <div className="space-y-3" role="group" aria-label="Reset your password">
          <div className="space-y-1.5">
            <Label htmlFor={fieldId}>Email address for the reset link</Label>
            <Input
              id={fieldId}
              type="email"
              autoComplete="email"
              value={email}
              disabled={busy}
              onChange={(event) => setEmail(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit(event);
              }}
            />
          </div>
          {error ? <InlineError>{error}</InlineError> : null}
          <Button type="button" size="sm" disabled={busy} onClick={(event) => void submit(event)}>
            {busy ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                Sending
              </>
            ) : (
              "Email me a reset link"
            )}
          </Button>
        </div>
      )}
      <button
        type="button"
        className="text-sm font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setOpen(false)}
      >
        Back to sign in
      </button>
    </div>
  );
}

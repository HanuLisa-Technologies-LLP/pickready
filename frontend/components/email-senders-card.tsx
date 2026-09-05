"use client";

// Corporate email senders (Corporate Email System spec, 2026-09-05).
//
// The org Settings surface for registering business mailboxes as authorized
// automated-email senders. The flow the dialogs drive is the spec's own:
// add sender -> business email check (server-side blocklist, errors surfaced
// verbatim) -> six-digit code sent to the mailbox -> POC enters the code ->
// ownership verified -> the client Super Admin authorizes -> Active.
//
// Status is always a WORD CHIP, never an icon alone and never a number. The
// server says what the caller may do (can_manage / can_authorize), so this
// renders only reachable controls instead of guessing at roles.

import * as React from "react";
import { Mail, Plus } from "lucide-react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type {
  EmailSender,
  EmailSenderList,
  EmailSenderOtpIssue,
  EmailSenderStatus,
  EmailSenderVerifyResult,
} from "@/lib/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { useToast } from "@/components/ui/toast";
import { InlineError, Section } from "@/components/page-primitives";

const STATUS_CHIP: Record<
  EmailSenderStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  pending_verification: { label: "Awaiting verification", variant: "outline" },
  email_verified: { label: "Verified", variant: "secondary" },
  active: { label: "Active", variant: "brand" },
  verification_expired: { label: "Verification expired", variant: "muted" },
  disabled: { label: "Disabled", variant: "muted" },
  revoked: { label: "Revoked", variant: "destructive" },
};

export function EmailSendersCard() {
  const { toast } = useToast();
  const [data, setData] = React.useState<EmailSenderList | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  // 403 means this account cannot manage senders; the card simply is not
  // part of their settings page rather than a dead end.
  const [hidden, setHidden] = React.useState(false);

  const [addOpen, setAddOpen] = React.useState(false);
  const [otpSender, setOtpSender] = React.useState<EmailSender | null>(null);
  const [cooldownUntil, setCooldownUntil] = React.useState(0);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [confirmRevokeId, setConfirmRevokeId] = React.useState<string | null>(
    null
  );

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      setData(await apiGet<EmailSenderList>("/email-senders"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setHidden(true);
        return;
      }
      setLoadError(apiErrorMessage(error));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const armCooldown = (seconds: number) => {
    setCooldownUntil(Date.now() + seconds * 1000);
  };

  const act = async (sender: EmailSender, action: string, label: string) => {
    setBusyId(sender.id);
    try {
      await apiPost<EmailSender>(`/email-senders/${sender.id}/${action}`);
      toast({ title: `${label}: ${sender.email}` });
      await load();
    } catch (error) {
      toast({
        title: `Could not ${label.toLowerCase()} this sender`,
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
      setConfirmRevokeId(null);
    }
  };

  const resend = async (sender: EmailSender, openDialog: boolean) => {
    setBusyId(sender.id);
    try {
      const issued = await apiPost<EmailSenderOtpIssue>(
        `/email-senders/${sender.id}/resend-otp`
      );
      armCooldown(issued.resend_cooldown_seconds);
      toast({ title: `Code sent to ${sender.email}` });
      await load();
      if (openDialog) setOtpSender({ ...sender, status: issued.status });
    } catch (error) {
      toast({
        title: "Could not send a code",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  };

  if (hidden) return null;

  const senders = data?.senders ?? [];
  const canAuthorize = data?.can_authorize ?? false;

  return (
    <Section
      title="Email senders"
      description="Business mailboxes your company has authorized for automated recruitment email. ReadyPick never asks for a mailbox password: ownership is proven by a code sent to the address."
      actions={
        <Button type="button" size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" /> Add sender
        </Button>
      }
      contentClassName="space-y-3"
    >
      {loadError ? <InlineError>{loadError}</InlineError> : null}

      {data && senders.length === 0 ? (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-secondary px-4 py-3 text-sm">
          <Mail className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            No senders yet. Add a business address like hr@yourcompany.com to
            send automated email under your own identity.
          </span>
        </div>
      ) : null}

      {senders.map((sender, index) => {
        const chip = STATUS_CHIP[sender.status];
        const busy = busyId === sender.id;
        return (
          <React.Fragment key={sender.id}>
            {index > 0 ? <Separator /> : null}
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{sender.email}</p>
                <p className="text-sm">{sender.name}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={chip.variant}>{chip.label}</Badge>
                {sender.status === "pending_verification" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => setOtpSender(sender)}
                  >
                    Enter code
                  </Button>
                ) : null}
                {sender.status === "verification_expired" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void resend(sender, true)}
                  >
                    Send a new code
                  </Button>
                ) : null}
                {canAuthorize && sender.status === "email_verified" ? (
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "authorize", "Authorized")}
                  >
                    Authorize
                  </Button>
                ) : null}
                {canAuthorize && sender.status === "active" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "disable", "Disabled")}
                  >
                    Disable
                  </Button>
                ) : null}
                {canAuthorize && sender.status === "disabled" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "enable", "Enabled")}
                  >
                    Enable
                  </Button>
                ) : null}
                {canAuthorize &&
                ["active", "disabled", "email_verified"].includes(
                  sender.status
                ) ? (
                  confirmRevokeId === sender.id ? (
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      disabled={busy}
                      onClick={() => void act(sender, "revoke", "Revoked")}
                    >
                      Confirm revoke
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => setConfirmRevokeId(sender.id)}
                    >
                      Revoke
                    </Button>
                  )
                ) : null}
              </div>
            </div>
          </React.Fragment>
        );
      })}

      <AddSenderDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={(sender, issued) => {
          setAddOpen(false);
          armCooldown(issued.resend_cooldown_seconds);
          void load();
          setOtpSender(sender);
        }}
      />

      <VerifyOtpDialog
        sender={otpSender}
        cooldownUntil={cooldownUntil}
        onResend={(sender) => void resend(sender, false)}
        onClose={() => setOtpSender(null)}
        onSettled={() => void load()}
      />
    </Section>
  );
}

function AddSenderDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (sender: EmailSender, issued: EmailSenderOtpIssue) => void;
}) {
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) {
      setName("");
      setEmail("");
      setError(null);
    }
  }, [open]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !email.trim()) {
      setError("Enter the contact name and the business email address.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const issued = await apiPost<EmailSenderOtpIssue>("/email-senders", {
        name: name.trim(),
        email: email.trim(),
      });
      onCreated(
        {
          id: issued.sender_id,
          name: name.trim(),
          email: email.trim().toLowerCase(),
          status: issued.status,
          email_verified: false,
          authorized_by: null,
          authorized_at: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
        issued
      );
    } catch (err) {
      // The blocklist refusal names the domain; show it verbatim.
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a sender</DialogTitle>
          <DialogDescription>
            A six-digit code will be sent to this address to prove your company
            controls the mailbox. Personal email providers are not accepted.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit} noValidate>
          <FormField label="Contact name" htmlFor="sender-name" required>
            <Input
              id="sender-name"
              maxLength={200}
              value={name}
              disabled={saving}
              onChange={(e) => setName(e.target.value)}
              placeholder="Rahul"
            />
          </FormField>
          <FormField label="Business email" htmlFor="sender-email" required>
            <Input
              id="sender-email"
              type="email"
              maxLength={320}
              value={email}
              disabled={saving}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="hr@yourcompany.com"
            />
          </FormField>
          {error ? <InlineError>{error}</InlineError> : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Sending code" : "Add and send code"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function VerifyOtpDialog({
  sender,
  cooldownUntil,
  onResend,
  onClose,
  onSettled,
}: {
  sender: EmailSender | null;
  cooldownUntil: number;
  onResend: (sender: EmailSender) => void;
  onClose: () => void;
  onSettled: () => void;
}) {
  const { toast } = useToast();
  const [code, setCode] = React.useState("");
  const [checking, setChecking] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = React.useState(0);

  React.useEffect(() => {
    if (sender) {
      setCode("");
      setError(null);
    }
  }, [sender]);

  // The resend countdown, ticking once a second while the dialog is open.
  React.useEffect(() => {
    if (!sender) return;
    const tick = () =>
      setSecondsLeft(Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000)));
    tick();
    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
  }, [sender, cooldownUntil]);

  if (!sender) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!/^[0-9]{6}$/.test(code)) {
      setError("Enter the six-digit code from the email.");
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const result = await apiPost<EmailSenderVerifyResult>(
        `/email-senders/${sender.id}/verify-otp`,
        { code }
      );
      if (result.verified) {
        toast({ title: `${sender.email} verified` });
        onSettled();
        onClose();
        return;
      }
      if (result.reason === "mismatch") {
        setError(
          result.attempts_remaining === 1
            ? "That code is not correct. One attempt left."
            : `That code is not correct. ${result.attempts_remaining} attempts left.`
        );
      } else if (result.reason === "expired") {
        setError("That code has expired. Send a new one to try again.");
        onSettled();
      } else if (result.reason === "attempts_exhausted") {
        setError("Too many incorrect attempts. Send a new code to try again.");
      } else {
        setError("No code is outstanding for this sender. Send a new one.");
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setChecking(false);
    }
  };

  return (
    <Dialog open onOpenChange={(next) => (!next ? onClose() : null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Verify {sender.email}</DialogTitle>
          <DialogDescription>
            Enter the six-digit code that was emailed to this mailbox. The code
            expires after a few minutes and allows three attempts.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit} noValidate>
          <FormField label="Verification code" htmlFor="sender-otp" required>
            <Input
              id="sender-otp"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              disabled={checking}
              onChange={(e) =>
                setCode(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))
              }
              placeholder="123456"
              className="text-center text-lg tracking-[0.5em]"
            />
          </FormField>
          {error ? <InlineError>{error}</InlineError> : null}
          <DialogFooter className="flex-wrap gap-2 sm:justify-between">
            <Button
              type="button"
              variant="outline"
              disabled={checking || secondsLeft > 0}
              onClick={() => onResend(sender)}
            >
              {secondsLeft > 0
                ? `Resend code in ${secondsLeft}s`
                : "Resend code"}
            </Button>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={checking}
                onClick={onClose}
              >
                Close
              </Button>
              <Button type="submit" disabled={checking || code.length !== 6}>
                {checking ? "Checking" : "Verify"}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

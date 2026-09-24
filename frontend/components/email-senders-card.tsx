"use client";

// Corporate email senders (Corporate Email System spec, 2026-09-05).
//
// The org Settings surface for registering business mailboxes as authorized
// automated-email senders. The flow: add sender -> business email check
// (server-side blocklist, errors surfaced verbatim) -> the client Super Admin
// authorizes -> Active. The six-digit mailbox code this comment used to
// describe was withdrawn on 2026-09-08 (see the note at the end of the file),
// and the Add dialog kept promising it until the vivekium release.
//
// Status is always a WORD CHIP, never an icon alone and never a number. The
// server says what the caller may do (can_manage / can_authorize), so this
// renders only reachable controls instead of guessing at roles.
//
// THE DEFAULT SENDER (vivekium release, Phase 6)
// `email_log.sender_id` was never written, so a company that registered and
// approved a corporate mailbox still had every email leave from the platform
// mailbox. The server now resolves a sender for EVERY candidate email: the one
// a recruiter picked, else the tenant's default, else the platform mailbox.
// This card is where the default is chosen. Only an ACTIVE sender can hold it,
// and the server clears it when the sender stops being active, so a revoked
// mailbox can never keep sending by default.

import * as React from "react";
import { Mail, Plus } from "lucide-react";

import { ApiError, apiDelete, apiGet, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type {
  EmailSender,
  EmailSenderList,
  EmailSenderStatus,
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
import { EmptyState, InlineError, Section } from "@/components/page-primitives";

const STATUS_CHIP: Record<
  EmailSenderStatus,
  { label: string; variant: BadgeProps["variant"] }
> = {
  // WORDS THE CLIENT'S SUPER ADMIN ACTUALLY USES. Every one of these
  // describes the company's own decision; none names a provider, an identity
  // or a verification mechanism.
  pending_verification: { label: "Awaiting approval", variant: "outline" },
  active: { label: "Active", variant: "brand" },
  disabled: { label: "Paused", variant: "muted" },
  revoked: { label: "Removed", variant: "destructive" },
  rejected: { label: "Rejected", variant: "destructive" },
  // Legacy, from before the mailbox code was withdrawn. Rows still carry
  // these, and a status with no chip renders as an empty badge.
  email_verified: { label: "Awaiting approval", variant: "outline" },
  verification_expired: { label: "Awaiting approval", variant: "outline" },
};

// The states a Super Admin decision is still outstanding on. The two OTP
// states are here because rows written before the code was withdrawn still
// carry them, and those senders must remain decidable rather than stranded.
const AWAITING_DECISION = [
  "pending_verification",
  "email_verified",
  "verification_expired",
];

// Every control on a sender row, as data: where it posts, and the two
// sentences the person is told. Written out whole rather than built from a
// verb, because "Could not " + "Approved".toLowerCase() is how this card used
// to tell somebody it "could not approved this sender".
type SenderAction =
  | "approve"
  | "reject"
  | "disable"
  | "enable"
  | "revoke"
  | "makeDefault"
  | "clearDefault";

const SENDER_ACTIONS: Record<
  SenderAction,
  { path: string; method: "post" | "delete"; done: string; failed: string }
> = {
  approve: {
    path: "approve",
    method: "post",
    done: "Approved",
    failed: "Could not approve this sender",
  },
  reject: {
    path: "reject",
    method: "post",
    done: "Rejected",
    failed: "Could not reject this sender",
  },
  disable: {
    path: "disable",
    method: "post",
    done: "Paused",
    failed: "Could not pause this sender",
  },
  enable: {
    path: "enable",
    method: "post",
    done: "Enabled",
    failed: "Could not enable this sender",
  },
  revoke: {
    path: "revoke",
    method: "post",
    done: "Removed",
    failed: "Could not remove this sender",
  },
  makeDefault: {
    path: "default",
    method: "post",
    done: "Default sender set",
    failed: "Could not make this the default sender",
  },
  clearDefault: {
    path: "default",
    method: "delete",
    done: "Default sender cleared",
    failed: "Could not stop using this as the default sender",
  },
};

export function EmailSendersCard() {
  const { toast } = useToast();
  const [data, setData] = React.useState<EmailSenderList | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  // 403 means this account cannot manage senders; the card simply is not
  // part of their settings page rather than a dead end.
  const [hidden, setHidden] = React.useState(false);

  const [addOpen, setAddOpen] = React.useState(false);
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

  const act = async (sender: EmailSender, action: SenderAction) => {
    const spec = SENDER_ACTIONS[action];
    setBusyId(sender.id);
    try {
      const path = `/email-senders/${sender.id}/${spec.path}`;
      if (spec.method === "delete") {
        await apiDelete<EmailSender>(path);
      } else {
        await apiPost<EmailSender>(path);
      }
      toast({ title: `${spec.done}: ${sender.email}` });
      await load();
    } catch (error) {
      toast({
        title: spec.failed,
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
      setConfirmRevokeId(null);
    }
  };

  if (hidden) return null;

  const senders = data?.senders ?? [];
  const canAuthorize = data?.can_authorize ?? false;

  return (
    <Section
      title="Email senders"
      description="Business mailboxes your company has authorized to send recruitment email. Vivekium never asks for a mailbox password. A new address stays pending until your Super Admin approves it. Email that does not name a sender goes out from your default sender, or from the Vivekium mailbox when none is set."
      actions={
        <Button type="button" size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" /> Add sender
        </Button>
      }
      contentClassName="space-y-3"
    >
      {loadError ? <InlineError>{loadError}</InlineError> : null}

      {data && senders.length === 0 ? (
        <EmptyState
          icon={Mail}
          title="No senders yet"
          description="Add a business address like hr@yourcompany.com to send automated email under your own identity."
          className="py-10"
        />
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
                {sender.is_default ? (
                  <Badge variant="outline">Default sender</Badge>
                ) : null}
                {canAuthorize &&
                sender.status === "active" &&
                !sender.is_default ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "makeDefault")}
                  >
                    Make default
                  </Button>
                ) : null}
                {canAuthorize && sender.is_default ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "clearDefault")}
                  >
                    Stop using as default
                  </Button>
                ) : null}
                {canAuthorize && AWAITING_DECISION.includes(sender.status) ? (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      disabled={busy}
                      onClick={() => void act(sender, "approve")}
                    >
                      Approve
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => void act(sender, "reject")}
                    >
                      Reject
                    </Button>
                  </>
                ) : null}
                {canAuthorize && sender.status === "active" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void act(sender, "disable")}
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
                    onClick={() => void act(sender, "enable")}
                  >
                    Enable
                  </Button>
                ) : null}
                {canAuthorize &&
                ["active", "disabled"].includes(sender.status) ? (
                  confirmRevokeId === sender.id ? (
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      disabled={busy}
                      onClick={() => void act(sender, "revoke")}
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
        onCreated={() => {
          setAddOpen(false);
          void load();
        }}
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
  onCreated: (sender: EmailSender) => void;
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
      // The server returns the sender it created, including whether the
      // address can actually send. Reusing that beats reconstructing a row
      // here from what was typed: the eligibility half cannot be guessed.
      onCreated(
        await apiPost<EmailSender>("/email-senders", {
          name: name.trim(),
          email: email.trim(),
        })
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
            Personal email providers are not accepted. The address stays
            pending until your Super Admin approves it.
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
              {saving ? "Adding" : "Add sender"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// The mailbox verification dialog was REMOVED on 2026-09-08 with the sender
// OTP. SES refuses to send as any identity the account has not verified, so a
// code typed into this portal re-proved on registration what AWS enforces on
// every send. What replaced it is not another dialog: the row now reports
// whether the address can actually send, in plain words.

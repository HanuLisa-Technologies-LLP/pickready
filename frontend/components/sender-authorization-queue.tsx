"use client";

// Sender Authorization: the client Super Admin decides which addresses may
// send email in this company's name.
//
// WHAT THIS SCREEN DELIBERATELY DOES NOT SAY
// ------------------------------------------
// Not SES, not SNS, not an identity, not DKIM, not a configuration set, not a
// region, not an ARN. Those are how the mail leaves; none of them is a
// decision anybody here makes. The one place infrastructure reality shows
// through is `sending_detail`, and the SERVER writes that sentence in the
// client's own vocabulary precisely so this component never has to translate.
//
// THE DECISION AND THE PLUMBING ARE SEPARATE, AND THE SCREEN SHOWS BOTH
// ---------------------------------------------------------------------
// Approving is the company authorizing an address to speak for it. Whether
// mail can physically leave is a different fact, and it can change after the
// decision is made. So an address whose setup is incomplete is still
// approvable: the note is SHOWN, not enforced. A transient provider problem
// must not veto a company's own authorization, and the send path re-checks
// anyway, so nothing unsafe follows from approving early.

import * as React from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type { EmailSender, EmailSenderList, EmailSenderStatus } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InlineError, Section } from "@/components/page-primitives";
import { Separator } from "@/components/ui/separator";

// The states a decision is still outstanding on. The two OTP-era states are
// here because rows written before the mailbox code was withdrawn still carry
// them, and those senders must stay decidable rather than stranded.
const AWAITING_DECISION: EmailSenderStatus[] = [
  "pending_verification",
  "email_verified",
  "verification_expired",
];

const DECIDED_CHIP: Partial<
  Record<EmailSenderStatus, { label: string; variant: BadgeProps["variant"] }>
> = {
  active: { label: "Active", variant: "brand" },
  disabled: { label: "Paused", variant: "muted" },
  rejected: { label: "Rejected", variant: "destructive" },
  revoked: { label: "Removed", variant: "destructive" },
};

export function SenderAuthorizationQueue() {
  const { toast } = useToast();
  const [data, setData] = React.useState<EmailSenderList | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  // 403 means this account cannot authorize senders. The page renders nothing
  // rather than an error: it is not their surface, and saying so in red would
  // read as a fault they could fix.
  const [hidden, setHidden] = React.useState(false);
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      const result = await apiGet<EmailSenderList>("/email-senders");
      // THE SERVER DECIDES, and this only renders what it was told. A
      // Recruitment Manager can read the sender list but holds no
      // authorization grant, so the controls must not appear for them even
      // though their fetch succeeded.
      if (!result.can_authorize) {
        setHidden(true);
        return;
      }
      setData(result);
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

  const decide = async (
    sender: EmailSender,
    action: "approve" | "reject",
    label: string
  ) => {
    setBusyId(sender.id);
    try {
      await apiPost<EmailSender>(`/email-senders/${sender.id}/${action}`);
      toast({ title: `${label}: ${sender.email}` });
      await load();
    } catch (error) {
      toast({
        title: `Could not ${action} this sender`,
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
    }
  };

  if (hidden) return null;

  const senders = data?.senders ?? [];
  const pending = senders.filter((s) => AWAITING_DECISION.includes(s.status));
  const decided = senders.filter((s) => !AWAITING_DECISION.includes(s.status));

  return (
    <div className="space-y-6">
      <Section
        title="Sender Authorization"
        description="Addresses your team has asked to send recruitment email from. Approving one authorizes it to write to candidates in your company's name."
      >
        {loadError ? <InlineError>{loadError}</InlineError> : null}

        {!loadError && pending.length === 0 ? (
          <p className="text-sm">
            No addresses are waiting for a decision. New requests appear here
            when someone on your team adds a sender in Settings.
          </p>
        ) : null}

        {pending.map((sender, index) => {
          const busy = busyId === sender.id;
          return (
            <React.Fragment key={sender.id}>
              {index > 0 ? <Separator /> : null}
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">
                    {sender.email}
                  </p>
                  <p className="text-sm">{sender.name}</p>
                  {/* Shown, never enforced. The company's authorization and
                      the address's readiness to send are separate facts. */}
                  {!sender.can_send ? (
                    <p className="mt-1 text-sm">{sender.sending_detail}</p>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">Awaiting your decision</Badge>
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy}
                    onClick={() => void decide(sender, "approve", "Approved")}
                  >
                    Approve
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void decide(sender, "reject", "Rejected")}
                  >
                    Reject
                  </Button>
                </div>
              </div>
            </React.Fragment>
          );
        })}
      </Section>

      {decided.length > 0 ? (
        <Section
          title="Decided"
          description="Addresses you have already ruled on. Pausing, resuming and removing an active sender stay in Settings."
        >
          {decided.map((sender, index) => {
            const chip = DECIDED_CHIP[sender.status];
            return (
              <React.Fragment key={sender.id}>
                {index > 0 ? <Separator /> : null}
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">
                      {sender.email}
                    </p>
                    <p className="text-sm">{sender.name}</p>
                    {/* An approved address that still cannot send is the one
                        case worth surfacing after the decision: the company
                        said yes and no mail is actually leaving. */}
                    {sender.status === "active" && !sender.can_send ? (
                      <p className="mt-1 text-sm">{sender.sending_detail}</p>
                    ) : null}
                  </div>
                  {chip ? (
                    <Badge variant={chip.variant}>{chip.label}</Badge>
                  ) : null}
                </div>
              </React.Fragment>
            );
          })}
        </Section>
      ) : null}
    </div>
  );
}

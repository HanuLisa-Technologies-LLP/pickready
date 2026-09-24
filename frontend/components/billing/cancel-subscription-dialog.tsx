"use client";

// Cancel a subscription, from inside the product.
//
// WHY IT EXISTS. POST /billing/cancel has existed, gated and tested, since the
// subscription work of 2026-07-28, and no screen called it. A paid monthly
// plan a customer cannot stop without writing to support is a consumer
// protection defect, not a missing nicety.
//
// WHAT IT PROMISES, AND ONLY THAT. Razorpay is asked to cancel AT CYCLE END,
// so the current period is not refunded or cut short and nothing is charged
// again. Credits already in the pool are never clawed back: they were paid
// for. The dialog says exactly those two things and nothing the server does
// not do.
//
// Cancelling is not reversible from this page (a new subscription is a new
// checkout), so it is behind the product's one confirmation pattern,
// `ConfirmButton`, which focuses Cancel first and never closes on a stray
// click.

import * as React from "react";

import { apiPost } from "@/lib/api";
import type { SubscriptionSummary } from "@/lib/types";
import { ConfirmButton } from "@/components/confirm-button";
import { useToast } from "@/components/ui/toast";

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function CancelSubscriptionDialog({
  subscription,
  onCancelled,
}: {
  subscription: SubscriptionSummary;
  onCancelled: (updated: SubscriptionSummary) => void | Promise<void>;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);

  const planName = subscription.plan?.name ?? "current";
  const periodEnd = subscription.current_end
    ? `at the end of the current billing period, on ${formatDate(
        subscription.current_end
      )}`
    : "at the end of the current billing period";

  const cancel = async () => {
    setBusy(true);
    try {
      const updated = await apiPost<SubscriptionSummary>("/billing/cancel");
      toast({
        title: "Subscription cancelled",
        description:
          "It will not renew and you will not be charged again. Your credits stay in your pool.",
      });
      await onCancelled(updated);
    } catch (error) {
      toast({
        variant: "destructive",
        title: "The subscription was not cancelled",
        description:
          error instanceof Error ? error.message : "Try again in a moment.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmButton
      variant="outline"
      disabled={busy}
      title={`Cancel your ${planName} subscription?`}
      description={`It stops renewing ${periodEnd}, and you will not be charged again. Credits already in your pool stay there and can still be used. To subscribe again later you start a new checkout.`}
      confirmLabel="Cancel subscription"
      cancelLabel="Keep subscription"
      onConfirm={() => void cancel()}
    >
      {busy ? "Cancelling" : "Cancel subscription"}
    </ConfirmButton>
  );
}

"use client";

// The credit pack cards and the order summary: ONE component for the two
// places a company buys credits, the billing page (a top-up) and company
// registration (the first purchase that activates the workspace). Every figure
// is the server's (`/billing/credit-packs` or `/company-onboarding/pricing`,
// both priced by `credit_packs.quote`): the picker renders the calculation and
// never performs it, so the two screens cannot quote one pack two ways.

import * as React from "react";
import { ArrowUpRight, Loader2 } from "lucide-react";

import type { CreditPack, CreditPacksResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";

export function formatInr(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

export function CreditPackPicker({
  packs,
  selectedSlug,
  onSelect,
  onBuy,
  busy,
  canBuy = true,
  cannotBuyNote,
  showEnterprise = true,
}: {
  packs: CreditPacksResponse;
  selectedSlug: string | null;
  onSelect: (slug: string | null) => void;
  onBuy: (pack: CreditPack) => void;
  busy: boolean;
  canBuy?: boolean;
  /** Shown under a disabled button, for somebody who may look but not buy. */
  cannotBuyNote?: string;
  /** The Enterprise card: custom volume by conversation, never self-serve. */
  showEnterprise?: boolean;
}) {
  // Hidden packs stay hidden: the trial card disappears after first use
  // (directive Part 5 section 3.1) and the selection dies with it.
  const availablePacks = packs.packs.filter((pack) => pack.available);
  const selectedPack =
    availablePacks.find((pack) => pack.slug === selectedSlug) ?? null;

  return (
    <>
      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {availablePacks.map((pack) => {
          const selected = pack.slug === selectedSlug;
          return (
            <button
              key={pack.slug}
              type="button"
              aria-pressed={selected}
              onClick={() => onSelect(selected ? null : pack.slug)}
              className={
                "flex flex-col rounded-xl border p-5 text-left transition-colors " +
                (selected
                  ? "border-brand-600 ring-1 ring-brand-600/30"
                  : "border-border hover:border-brand-600/50")
              }
            >
              <p className="text-2xl font-semibold tabular-nums">
                {pack.credits}
                <span className="ml-1 text-sm font-medium">credits</span>
              </p>
              {pack.trial ? (
                <p className="mt-1 type-eyebrow">Trial, first purchase only</p>
              ) : null}
              {pack.bonus_credits > 0 ? (
                <p className="mt-1 text-sm font-medium">
                  +{pack.bonus_credits} bonus credits free
                </p>
              ) : null}
              <div className="flex-1" />
              <p className="mt-3 font-semibold">{formatInr(pack.total_inr)}</p>
              <p className="text-xs">one-time, incl. GST</p>
            </button>
          );
        })}
        {showEnterprise ? (
          // Custom volume is Enterprise, by conversation and never self-serve
          // (directive Part 5 section 3.2).
          <a
            href="mailto:hello@pickready.app?subject=Enterprise%20credits"
            className="flex flex-col rounded-xl border border-border p-5 transition-colors hover:border-brand-600/50"
          >
            <p className="text-2xl font-semibold tabular-nums">Custom</p>
            <p className="mt-1 text-sm">
              {packs.min_custom_credits}+ credits, priced by agreement. No
              self-serve checkout.
            </p>
            <div className="flex-1" />
            <p className="mt-3 inline-flex items-center gap-1 font-semibold underline">
              Talk to us about Enterprise
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
            </p>
          </a>
        ) : null}
      </div>

      {/* Live breakdown of the selected pack (directive Part 5 section 3.3
          step 2). Every figure below is the server's. */}
      {selectedPack ? (
        <div className="mt-6 max-w-md rounded-xl border border-border bg-surface p-5">
          <p className="font-semibold">Order summary</p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-4">
              <dt>Credits</dt>
              <dd className="font-medium">{selectedPack.credits}</dd>
            </div>
            {selectedPack.bonus_credits > 0 ? (
              <div className="flex justify-between gap-4">
                <dt>Bonus credits</dt>
                <dd className="font-medium">+{selectedPack.bonus_credits} free</dd>
              </div>
            ) : null}
            <div className="flex justify-between gap-4">
              <dt>Subtotal</dt>
              <dd className="font-medium">{formatInr(selectedPack.subtotal_inr)}</dd>
            </div>
            {selectedPack.setup_fee_inr > 0 || selectedPack.setup_fee_waived ? (
              <div className="flex justify-between gap-4">
                <dt>Account setup fee</dt>
                <dd className="font-medium">
                  {selectedPack.setup_fee_waived
                    ? "Waived"
                    : formatInr(selectedPack.setup_fee_inr)}
                </dd>
              </div>
            ) : null}
            <div className="flex justify-between gap-4">
              <dt>GST @ {packs.gst_rate_percent}%</dt>
              <dd className="font-medium">{formatInr(selectedPack.gst_inr)}</dd>
            </div>
            <div className="flex justify-between gap-4 border-t border-border pt-2">
              <dt className="font-semibold">Total payable</dt>
              <dd className="text-base font-semibold tabular-nums">
                {formatInr(selectedPack.total_inr)}
              </dd>
            </div>
          </dl>
          <Button
            className="mt-5 w-full"
            disabled={!canBuy || busy}
            onClick={() => onBuy(selectedPack)}
          >
            {busy ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Working
              </>
            ) : (
              "Proceed to Payment"
            )}
          </Button>
          {!canBuy && cannotBuyNote ? (
            <p className="mt-2 text-xs">{cannotBuyNote}</p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

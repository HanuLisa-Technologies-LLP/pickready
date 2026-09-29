"use client";

// Starter top-up picker. All prices and credit counts come from the server.

import * as React from "react";
import { Loader2 } from "lucide-react";

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
}: {
  packs: CreditPacksResponse;
  selectedSlug: string | null;
  onSelect: (slug: string | null) => void;
  onBuy: (pack: CreditPack) => void;
  busy: boolean;
  canBuy?: boolean;
  /** Shown under a disabled button, for somebody who may look but not buy. */
  cannotBuyNote?: string;
}) {
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
              <p className="font-semibold">{pack.label}</p>
              <p className="mt-2 text-2xl font-semibold tabular-nums">
                {pack.credits_total}
                <span className="ml-1 text-sm font-medium">completed assessments</span>
              </p>
              <div className="flex-1" />
              <p className="mt-3 font-semibold">{formatInr(pack.total_inr)}</p>
              <p className="text-xs">one-time, incl. GST</p>
            </button>
          );
        })}
      </div>

      {/* Live breakdown of the selected pack (directive Part 5 section 3.3
          step 2). Every figure below is the server's. */}
      {selectedPack ? (
        <div className="mt-6 max-w-md rounded-xl border border-border bg-surface p-5">
          <p className="font-semibold">Order summary</p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-4">
              <dt>Completed assessments</dt>
              <dd className="font-medium">{selectedPack.credits_total}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>Subtotal</dt>
              <dd className="font-medium">{formatInr(selectedPack.subtotal_inr)}</dd>
            </div>
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

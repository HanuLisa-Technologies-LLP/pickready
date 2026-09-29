import type { Metadata } from "next";

import { publicPageMetadata } from "@/lib/site";

import { PricingCatalogue } from "./pricing-catalogue";

// The public pricing page (owner spec 2026-09-29, section 4.2). The landing
// page no longer carries the price list inline; "See pricing plans" opens this
// page from the header, the footer and the landing page's closing action.
//
// Every figure on it is the server's published catalogue
// (`GET /billing/public/credit-packs`), rendered by the client component the
// same way the public employer pages read their data. There is no price in
// this tree and no cached fallback: if the catalogue cannot be read, the page
// says so rather than showing a figure checkout might not charge.

export const metadata: Metadata = publicPageMetadata({
  path: "/pricing",
  title: "Pricing",
  description:
    "ReadyPick credit pricing: one rate per credit, bought in one-time packs, with GST, validity and bonus credits stated up front.",
});

export default function PricingPage() {
  return (
    <main id="main">
      <PricingCatalogue />
    </main>
  );
}

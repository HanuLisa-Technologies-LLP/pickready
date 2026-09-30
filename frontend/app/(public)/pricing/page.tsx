import type { Metadata } from "next";

import { publicPageMetadata } from "@/lib/site";

import { PricingCatalogue } from "./pricing-catalogue";

// The public pricing page is also linked from the header and footer. The same
// catalogue appears inside the landing page's Start section beside Open a role.
//
// Every figure on it is the server's published catalogue
// (`GET /billing/public/plans`), rendered by the client component the
// same way the public employer pages read their data. There is no price in
// this tree and no cached fallback: if the catalogue cannot be read, the page
// says so rather than showing a figure checkout might not charge.

export const metadata: Metadata = publicPageMetadata({
  path: "/pricing",
  title: "Pricing",
  description:
    "ReadyPick monthly plans with completed assessment allowances, three-month credit rollover, and every feature included.",
});

export default function PricingPage() {
  return (
    <main id="main" className="mx-auto max-w-7xl px-6 py-16">
      <h1 className="mb-3 type-section-title">Monthly plans</h1>
      <p className="mb-10 max-w-2xl type-lead">Choose the capacity your team needs. Every plan includes the complete platform.</p>
      <PricingCatalogue />
    </main>
  );
}

// @vitest-environment jsdom
//
// The public pricing page renders the SERVER's price list and nothing else
// (owner spec 2026-09-29, section 4.2). The load-bearing claims: every figure
// on the page comes from `GET /billing/public/credit-packs`; when that call
// fails the page shows an error and NO price at all; the source file holds no
// price literal that could drift from what checkout charges; and the company
// actions go to the company registration and sign-in routes.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", () => api);
// Motion is presentation. jsdom has no IntersectionObserver, and what is under
// test is which figures render, not how they fade in.
vi.mock("@/components/motion", () => ({
  Reveal: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  RevealStagger: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  StaggerItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
}));

import { PricingCatalogue, creditsText } from "./pricing-catalogue";
import type { PublishedCatalogue } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Deliberately NOT the product's real figures, so a page that rendered a
 *  literal of its own instead of the response would fail these assertions. */
const CATALOGUE: PublishedCatalogue = {
  price_per_credit_inr: 777,
  gst_rate_percent: 11,
  subunits_per_credit: 60,
  credit_validity_months: 7,
  min_custom_credits: 44,
  setup_fee_inr: 4321,
  setup_fee_gst_inr: 475,
  setup_fee_waiver_limit: 9,
  bonus_levels: [{ min_credits: 120, bonus_credits: 6 }],
  packs: [
    {
      slug: "trial_x",
      label: "Trial pack label",
      credits: 12,
      bonus_credits: 0,
      credits_total: 12,
      subtotal_inr: 9324,
      gst_inr: 1026,
      total_inr: 10350,
      new_accounts_only: true,
      validity_months: 7,
    },
    {
      slug: "volume_x",
      label: "Volume pack label",
      credits: 120,
      bonus_credits: 6,
      credits_total: 126,
      subtotal_inr: 93240,
      gst_inr: 10256,
      total_inr: 103496,
      new_accounts_only: false,
      validity_months: 7,
    },
  ],
  consumption: [
    {
      event_type: "completed_assessment",
      label: "Assessment completed",
      non_stem_subunits: 60,
      stem_subunits: 90,
    },
    {
      event_type: "no_show",
      label: "Invitation never opened",
      non_stem_subunits: 4,
      stem_subunits: 4,
    },
  ],
};

function inr(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

describe("the public pricing page", () => {
  it("asks the public catalogue route and renders its figures", async () => {
    api.apiGet.mockResolvedValueOnce(CATALOGUE);
    render(<PricingCatalogue />);

    await waitFor(() => expect(screen.getByText("Trial pack label")).toBeTruthy());
    expect(api.apiGet).toHaveBeenCalledWith("/billing/public/credit-packs");
    const text = document.body.textContent ?? "";
    expect(text).toContain(`${inr(777)} per credit`);
    expect(text).toContain(inr(10350));
    expect(text).toContain(inr(103496));
    expect(text).toContain("GST at 11%");
    expect(text).toContain("120 purchased + 6 bonus credits free");
    expect(text).toContain("New accounts only");
    expect(text).toContain(inr(4321));
    expect(text).toContain("first 9 client accounts");
    expect(text).toContain("valid for 7 months");
    expect(text).toContain("granted before expiry was introduced");
    expect(text).toContain("1.5 credits");
    expect(text).toContain("1/15 of a credit");
    expect(text).not.toMatch(/subscription plan|per month|monthly plan/i);
  });

  it("sends a company to registration and to company sign-in", async () => {
    api.apiGet.mockResolvedValueOnce(CATALOGUE);
    render(<PricingCatalogue />);
    await waitFor(() => expect(screen.getByText("Trial pack label")).toBeTruthy());

    const register = screen.getAllByRole("link", { name: /register company/i });
    const login = screen.getAllByRole("link", { name: /company login/i });
    expect(register.length).toBeGreaterThan(0);
    expect(login.length).toBeGreaterThan(0);
    for (const link of register) expect(link.getAttribute("href")).toBe("/company/register");
    for (const link of login) expect(link.getAttribute("href")).toBe("/company/login");
  });

  it("shows an error and no price at all when the catalogue cannot be read", async () => {
    api.apiGet.mockRejectedValueOnce(new Error("Service unavailable"));
    render(<PricingCatalogue />);

    await waitFor(() =>
      expect(screen.getByText(/price list could not be loaded/i)).toBeTruthy()
    );
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/₹|Rs\.|per credit/);
  });
});

describe("the rate wording", () => {
  it("states whole and half credits as decimals and finer rates exactly", () => {
    expect(creditsText(60, 60)).toBe("1 credit");
    expect(creditsText(90, 60)).toBe("1.5 credits");
    expect(creditsText(30, 60)).toBe("0.5 credits");
    expect(creditsText(20, 60)).toBe("1/3 of a credit");
    expect(creditsText(4, 60)).toBe("1/15 of a credit");
    expect(creditsText(3, 60)).toBe("1/20 of a credit");
  });
});

describe("the source", () => {
  it("holds no price literal of its own", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(join(here, "pricing-catalogue.tsx"), "utf-8");
    // A three-or-more digit number in the component would be a price, a pack
    // size or a fee typed a second time. The catalogue is the only author.
    // (A Tailwind scale step such as `text-teal-700` follows a hyphen and is
    // not a figure.)
    expect(source.match(/(?<![-\w])\d{3,}\b/g) ?? []).toEqual([]);
    expect(source).toContain('"/billing/public/credit-packs"');
  });
});

// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", () => api);
import { PricingCatalogue } from "./pricing-catalogue";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const catalogue = {
  plans: [
    { slug: "starter", name: "Starter", assessments: 75, price_inr: 24000, gst_inr: 4320, total_inr: 28320, rollover_months: 3 },
    { slug: "growth", name: "Growth", assessments: 200, price_inr: 55000, gst_inr: 9900, total_inr: 64900, rollover_months: 3 },
    { slug: "scale", name: "Scale", assessments: 500, price_inr: 120000, gst_inr: 21600, total_inr: 141600, rollover_months: 3 },
    { slug: "pro", name: "Pro", assessments: 1200, price_inr: 240000, gst_inr: 43200, total_inr: 283200, rollover_months: 3 },
  ],
  gst_rate_percent: 18, pilot_days: 30,
  pilot_plan_slug: "starter", topup_plan_slug: "starter",
};

describe("monthly pricing", () => {
  it("renders server-owned prices, volume and prominent rollover for all four plans", async () => {
    api.apiGet.mockResolvedValueOnce(catalogue);
    render(<PricingCatalogue />);
    await waitFor(() => expect(screen.getByText("Starter")).toBeTruthy());
    expect(api.apiGet).toHaveBeenCalledWith("/billing/public/plans");
    const text = document.body.textContent ?? "";
    for (const plan of catalogue.plans) {
      expect(text).toContain(new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(plan.price_inr));
      expect(text).toContain(new Intl.NumberFormat("en-IN").format(plan.assessments));
    }
    expect(screen.getAllByText(/Unused monthly credits roll over for 3 months, then expire/i)).toHaveLength(4);
    expect(text).toContain("all 7 agents");
    expect(text).toContain("BGV reconfirm");
    expect(text).toContain("across all open jobs");
    expect(text).toContain("No annual commitment or lock-in");
    expect(text).not.toMatch(/₹320|₹275|₹240\b|₹200\b/);
  });

  it("shows no price when the catalogue is unavailable", async () => {
    api.apiGet.mockRejectedValueOnce(new Error("offline"));
    render(<PricingCatalogue />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/₹|Rs\./);
  });
});

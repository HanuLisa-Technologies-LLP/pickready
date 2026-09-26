// @vitest-environment jsdom
//
// The billing page's credit statement and its cancel control (PLAN-p7 WP-B6).
// Both routes existed and were tested server-side while no screen called
// them. The load-bearing claims here: the statement pages through the ledger
// route rather than showing only the overview's newest rows, it never renders
// the application a consumption row refers to, and cancelling happens only
// after an explicit confirmation and then reports what the server returned.

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { CancelSubscriptionDialog } from "./cancel-subscription-dialog";
import { CreditStatement, STATEMENT_PAGE_SIZE } from "./credit-statement";
import type { CreditLedgerEntry, SubscriptionSummary } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const LINK_ID = "7a1c0f7e-0000-4000-8000-00000000abcd";

function entry(index: number, overrides: Partial<CreditLedgerEntry> = {}) {
  return {
    id: `row-${index}`,
    event_type: "completed_assessment",
    subunits_delta: -60,
    credits_delta: "-1.00",
    created_at: "2026-09-20T10:00:00Z",
    job_candidate_link_id: LINK_ID,
    ...overrides,
  } satisfies CreditLedgerEntry;
}

describe("credit statement", () => {
  it("asks the ledger route for one page plus one row, and says there is more", async () => {
    api.apiGet.mockResolvedValueOnce(
      Array.from({ length: STATEMENT_PAGE_SIZE + 1 }, (_, i) => entry(i))
    );
    render(<CreditStatement refreshToken="t1" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /older/i })).toBeTruthy()
    );
    expect(api.apiGet).toHaveBeenCalledWith(
      `/billing/ledger?skip=0&limit=${STATEMENT_PAGE_SIZE + 1}`
    );
    // The extra row is a probe, never rendered: exactly one page of rows.
    expect(screen.getAllByText("Assessment completed")).toHaveLength(
      STATEMENT_PAGE_SIZE * 2
    );
    expect(
      (screen.getByRole("button", { name: /older/i }) as HTMLButtonElement)
        .disabled
    ).toBe(false);
  });

  it("walks to the older page with the next offset", async () => {
    api.apiGet
      .mockResolvedValueOnce(
        Array.from({ length: STATEMENT_PAGE_SIZE + 1 }, (_, i) => entry(i))
      )
      .mockResolvedValueOnce([entry(99, { event_type: "grant", subunits_delta: 600, credits_delta: "10.00" })]);
    render(<CreditStatement refreshToken="t1" />);
    await waitFor(() => screen.getByRole("button", { name: /older/i }));

    fireEvent.click(screen.getByRole("button", { name: /older/i }));

    await waitFor(() =>
      expect(api.apiGet).toHaveBeenLastCalledWith(
        `/billing/ledger?skip=${STATEMENT_PAGE_SIZE}&limit=${STATEMENT_PAGE_SIZE + 1}`
      )
    );
    await waitFor(() => expect(screen.getAllByText("Credits added").length).toBe(2));
    expect(screen.getAllByText("+10.00").length).toBe(2);
    expect(screen.getByText("Page 2")).toBeTruthy();
  });

  it("names every event in words and never renders the application id", async () => {
    api.apiGet.mockResolvedValueOnce([
      entry(1, { event_type: "expiry", credits_delta: "-2.00", subunits_delta: -120 }),
      entry(2, { event_type: "no_show", credits_delta: "-0.07", subunits_delta: -4 }),
    ]);
    const { container } = render(<CreditStatement refreshToken="t1" />);

    await waitFor(() => expect(screen.getAllByText("Credits expired").length).toBe(2));
    expect(screen.getAllByText("Invitation never opened").length).toBe(2);
    expect(container.textContent).not.toContain(LINK_ID);
    expect(container.textContent).not.toContain("expiry");
    expect(container.textContent).not.toContain(String.fromCharCode(8212));
    // One page, no pager.
    expect(screen.queryByRole("button", { name: /older/i })).toBeNull();
  });

  it("states an empty ledger as a fact rather than an empty table", async () => {
    api.apiGet.mockResolvedValueOnce([]);
    render(<CreditStatement refreshToken="t1" />);
    await waitFor(() => expect(screen.getByText(/nothing yet/i)).toBeTruthy());
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reports a failed load with a retry, never an empty statement", async () => {
    api.apiGet.mockRejectedValueOnce(new Error("Service unavailable"));
    render(<CreditStatement refreshToken="t1" />);
    await waitFor(() =>
      expect(screen.getByText(/could not load the credit statement/i)).toBeTruthy()
    );
    expect(screen.queryByText(/nothing yet/i)).toBeNull();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});

const SUBSCRIPTION: SubscriptionSummary = {
  plan: {
    id: "plan-1",
    slug: "growth",
    name: "Growth",
    applications_per_month: 50,
    price_inr: 4999,
    rate_per_application_inr: 99,
    is_active: true,
    checkout_ready: true,
  },
  status: "active",
  razorpay_subscription_id: "sub_123",
  current_end: "2026-10-20T00:00:00Z",
};

describe("cancel subscription", () => {
  it("does nothing until the cancellation is confirmed", async () => {
    render(
      <CancelSubscriptionDialog subscription={SUBSCRIPTION} onCancelled={vi.fn()} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Cancel your Growth subscription?");
    expect(dialog.textContent).toMatch(/not be charged again/i);
    expect(dialog.textContent).toMatch(/credits already in your pool stay/i);
    fireEvent.click(screen.getByRole("button", { name: "Keep subscription" }));
    expect(api.apiPost).not.toHaveBeenCalled();
  });

  it("cancels through the one route and hands the result back", async () => {
    const cancelled = { ...SUBSCRIPTION, status: "cancelled" as const };
    api.apiPost.mockResolvedValueOnce(cancelled);
    const onCancelled = vi.fn();
    render(
      <CancelSubscriptionDialog subscription={SUBSCRIPTION} onCancelled={onCancelled} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));
    await screen.findByRole("alertdialog");
    const confirm = screen
      .getAllByRole("button", { name: "Cancel subscription" })
      .find((button) => button.closest("[role=alertdialog]"));
    expect(confirm).toBeTruthy();
    fireEvent.click(confirm!);

    await waitFor(() => expect(onCancelled).toHaveBeenCalledWith(cancelled));
    expect(api.apiPost).toHaveBeenCalledWith("/billing/cancel");
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Subscription cancelled" })
    );
  });

  it("says plainly when the server refused, and reports nothing as done", async () => {
    api.apiPost.mockRejectedValueOnce(new Error("There is no subscription to cancel."));
    const onCancelled = vi.fn();
    render(
      <CancelSubscriptionDialog subscription={SUBSCRIPTION} onCancelled={onCancelled} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));
    await screen.findByRole("alertdialog");
    fireEvent.click(
      screen
        .getAllByRole("button", { name: "Cancel subscription" })
        .find((button) => button.closest("[role=alertdialog]"))!
    );

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          variant: "destructive",
          description: "There is no subscription to cancel.",
        })
      )
    );
    expect(onCancelled).not.toHaveBeenCalled();
  });
});

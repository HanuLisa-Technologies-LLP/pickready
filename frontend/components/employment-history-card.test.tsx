// @vitest-environment jsdom
//
// The employment history card after submission. What is pinned:
//
// * the HR address correction appears on exactly the employers the server
//   marks `correction_needed`, and posts ONE field to the correction route;
// * the server's refusal sentence is shown verbatim;
// * a failed load is said, while a 404 (no candidate record yet) hides the
//   card as a normal state.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiGet: vi.fn(), apiPut: vi.fn(), apiPost: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { ApiError } from "@/lib/api";
import { EmploymentHistoryCard } from "./employment-history-card";

function employer(id: string, name: string, correction_needed: boolean) {
  return {
    id,
    employer_name: name,
    designation: "Engineer",
    started_on: "2022-01-01",
    ended_on: "2024-01-01",
    hr_name: "HR Desk",
    hr_email: `hr@${name.toLowerCase()}.example`,
    correction_needed,
  };
}

const HISTORY = {
  background: "experienced",
  finalized: true,
  finalized_at: "2026-09-01T10:00:00+00:00",
  submission_warning: "These details cannot be changed after you submit them.",
  employments: [employer("e1", "Acme", true), employer("e2", "Globex", false)],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the HR address correction", () => {
  it("is offered only where the server marks a bounce, and sends one field", async () => {
    http.apiGet.mockResolvedValue(HISTORY);
    http.apiPut.mockResolvedValue({
      ...HISTORY,
      employments: [employer("e1", "Acme", false), employer("e2", "Globex", false)],
    });
    render(<EmploymentHistoryCard />);

    const fields = await screen.findAllByLabelText(/corrected hr email/i);
    expect(fields).toHaveLength(1);

    fireEvent.change(fields[0], { target: { value: "people@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: /update address and resend/i }));

    await waitFor(() =>
      expect(http.apiPut).toHaveBeenCalledWith("/bgv/me/employers/e1/hr-email", {
        hr_email: "people@acme.example",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText(/corrected hr email/i)).toBeNull(),
    );
    expect(toast).toHaveBeenCalled();
  });

  it("shows the server's refusal in its own words", async () => {
    http.apiGet.mockResolvedValue(HISTORY);
    http.apiPut.mockRejectedValue(
      new ApiError(409, {
        detail: "That is the address we already tried. Please check it with the employer and enter the corrected one.",
      }),
    );
    render(<EmploymentHistoryCard />);
    const field = await screen.findByLabelText(/corrected hr email/i);
    fireEvent.change(field, { target: { value: "hr@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: /update address and resend/i }));
    expect(await screen.findByText(/the address we already tried/i)).toBeTruthy();
  });
});

describe("loading", () => {
  it("says a failed load rather than rendering nothing", async () => {
    http.apiGet.mockRejectedValue(new ApiError(503, { detail: "Service unavailable" }));
    render(<EmploymentHistoryCard />);
    expect(await screen.findByText(/could not be loaded/i)).toBeTruthy();
  });

  it("renders nothing for a person with no candidate record yet", async () => {
    http.apiGet.mockRejectedValue(new ApiError(404, { detail: "Candidate not found" }));
    const { container } = render(<EmploymentHistoryCard />);
    await waitFor(() => expect(http.apiGet).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

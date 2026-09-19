// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
}));
const { apiGet } = api;

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/components/matching-categories", () => ({
  MatchingCategoriesCard: () => null,
}));
vi.mock("@/components/proctoring/monitoring-policy-card", () => ({
  MonitoringPolicyCard: () => null,
}));

import { JobSetupReview, SetupStatus, type Setup } from "./job-setup-review";

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
});

function pendingSetup(overrides: Partial<Setup> = {}): Setup {
  return {
    job_id: "workify-job",
    status: "questions_pending_review",
    grade: "non_managerial",
    framework_approved: false,
    ready_for_candidates: false,
    framework_pending: false,
    ...overrides,
  };
}

function framework(overrides: Record<string, unknown> = {}) {
  return {
    job_id: "workify-job",
    status: "questions_pending_review",
    approved: false,
    competencies: [
      {
        id: "c-1",
        category: "must_have",
        name: "Distributed systems design",
        description: "Partitioning, replication and failure handling.",
        required_level: "Highly Matching",
        ordinal: 0,
      },
      {
        id: "c-2",
        category: "nice_to_have",
        name: "Terraform",
        description: null,
        required_level: "Matching",
        ordinal: 0,
      },
    ],
    maximum_items: 20,
    question_target: 20,
    minimum_per_category: 1,
    blocking_reason: null,
    ...overrides,
  };
}

function mockReads() {
  apiGet.mockImplementation((path: string) =>
    path.endsWith("/setup")
      ? Promise.resolve(pendingSetup())
      : Promise.resolve(framework())
  );
}

describe("SetupStatus", () => {
  it("explains the invitation block and links directly to the required action", () => {
    render(<SetupStatus setup={pendingSetup()} />);

    expect(screen.getByText("Job setup pending review")).toBeTruthy();
    expect(
      screen.getByText(/No candidate can be invited.*save the evaluation matrix/s)
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Review and save" }).getAttribute("href")
    ).toBe("#ppi-framework");
  });

  it("does not offer approval while framework generation is still running", () => {
    render(<SetupStatus setup={pendingSetup({ framework_pending: true })} />);

    expect(screen.getByText(/still writing the criteria/i)).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: "Review and save" })
    ).toBeNull();
  });

  it("points an unfinished intake at its home on the job description tab", () => {
    render(<SetupStatus setup={pendingSetup({ swot_complete: false })} />);

    expect(
      screen.getByText(/reporting authority intake.*job\s+description tab/is)
    ).toBeTruthy();
  });
});

describe("JobSetupReview", () => {
  it("no longer renders the intake here: it lives inside the SWOT panel on the JD tab", async () => {
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Tatva Assessment matrix");
    expect(screen.queryByText("Reporting authority intake")).toBeNull();
    expect(screen.queryByText("Role intake")).toBeNull();
  });

  it("renders each competency as a card with its name, controls and required grade word", async () => {
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Distributed systems design");
    // The grade is the word, never a number, on a solid badge.
    expect(screen.getAllByText("This role requires:")).toHaveLength(2);
    expect(screen.getByText("Highly Matching")).toBeTruthy();
    // Edit and remove sit on the card while the matrix is not frozen.
    expect(
      screen.getByRole("button", { name: "Edit Distributed systems design" })
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Remove Distributed systems design" })
    ).toBeTruthy();
    // The cards sit in a horizontal, scrollable row per category.
    const card = screen
      .getByText("Distributed systems design")
      .closest("div[draggable]");
    expect(card).toBeTruthy();
    expect(card?.parentElement?.className).toContain("overflow-x-auto");
    expect(card?.className).toContain("min-w-[190px]");
  });

  it("hides the mutation controls once the matrix is frozen", async () => {
    apiGet.mockImplementation((path: string) =>
      path.endsWith("/setup")
        ? Promise.resolve(pendingSetup({ framework_approved: true }))
        : Promise.resolve(framework({ approved: true }))
    );
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Distributed systems design");
    expect(
      screen.queryByRole("button", { name: "Edit Distributed systems design" })
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Add to Must-have/ })
    ).toBeNull();
  });
});

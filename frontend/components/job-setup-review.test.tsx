// @vitest-environment jsdom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
}));
const { apiGet } = api;

vi.mock("@/lib/api", () => api);

// THE MOCKED `toast` MUST BE STABLE ACROSS RENDERS, and this is not a detail.
//
// The real `useToast` returns a `useCallback` whose identity never changes, and
// `JobSetupReview` builds `load` from it and runs `load` in an effect keyed on
// its identity. A mock handing back a fresh `vi.fn()` per render therefore
// makes the component refetch on every render, forever: "Maximum update depth
// exceeded", a spinning worker, and a suite that looks slow rather than broken.
// The first test to interact twice with the screen is the one that exposes it.
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
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

  it("points an unsaved Job SWOT document at the job description tab", () => {
    render(<SetupStatus setup={pendingSetup({ swot_analysis_ready: false })} />);

    expect(
      screen.getByText(/Save the Job SWOT Analysis.*job description tab/is)
    ).toBeTruthy();
  });
});

describe("JobSetupReview", () => {
  it("does not render the removed Role Intake", async () => {
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Tatva Assessment matrix");
    expect(screen.queryByText("Reporting authority intake")).toBeNull();
    expect(screen.queryByText("Role intake")).toBeNull();
  });

  it("renders each competency as a chip carrying its name and required grade word", async () => {
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Distributed systems design");
    // The grade is the word, never a number, on a solid badge beside the name.
    expect(screen.getByText("Highly Matching")).toBeTruthy();
    // Edit and remove sit on the chip while the matrix is not frozen.
    expect(
      screen.getByRole("button", { name: "Edit Distributed systems design" })
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Remove Distributed systems design" })
    ).toBeTruthy();
    // Still draggable: moving an entry between Must-have and Nice-to-have is
    // spec 5.3 and survived the 2026-09-20 simplification.
    expect(
      screen.getByText("Distributed systems design").closest("div[draggable]")
    ).toBeTruthy();
  });

  it("asks for a name and a level, and nothing else", async () => {
    // The "What this measures" box is GONE from both the add and the edit
    // control (owner, 2026-09-20). It was optional, unexplained and never
    // asked for, and it made adding one skill a four-field form.
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Distributed systems design");
    expect(screen.queryByPlaceholderText(/what this measures/i)).toBeNull();

    // Opening the edit control must not reintroduce it.
    fireEvent.click(
      screen.getByRole("button", { name: "Edit Distributed systems design" })
    );
    await screen.findByLabelText("Rename Distributed systems design");
    expect(screen.queryByPlaceholderText(/what this measures/i)).toBeNull();
  });

  it("offers a one-line add per aspect plus a paste-a-list escape hatch", async () => {
    mockReads();
    render(<JobSetupReview jobId="workify-job" />);

    await screen.findByText("Distributed systems design");
    for (const aspect of ["Must-have", "Nice-to-have", "Behavioural Competencies"]) {
      expect(screen.getByLabelText(`Add to ${aspect}`)).toBeTruthy();
    }
    expect(screen.getAllByRole("button", { name: "Paste a list" })).toHaveLength(3);
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
    expect(screen.queryByLabelText("Add to Must-have")).toBeNull();
    expect(screen.queryByRole("button", { name: "Paste a list" })).toBeNull();
  });
});

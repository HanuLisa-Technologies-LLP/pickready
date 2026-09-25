// @vitest-environment jsdom

/**
 * The table: tab order, row states, and the two things it must never do.
 *
 * The accessibility assertions here are a GATE, not polish (spec-doc6 §8.3).
 * The tab order is the specification's own list and it is the order a
 * recruiter's hands learn; changing it silently is the kind of regression that
 * is only noticed by the people who cannot see the table.
 */

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
vi.mock("@/lib/api", () => ({
  apiGet: (path: string) => apiGet(path),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}));

import { STATE_HIGHLY, STATE_NOT_CHECKED, STATE_UNDER_REVIEW } from "./grade";
import { CandidateDashboard } from "./candidate-dashboard";
import { controls, row } from "./test-fixtures";
import type { DashboardPage } from "./types";

function page(overrides: Partial<DashboardPage> = {}): DashboardPage {
  return {
    columns: [
      "candidate",
      "source",
      "ai_match",
      "ready_pick_grade",
      "ready_pick_note",
      "ready_pick_profile",
      "team_review",
      "stage",
    ],
    column_labels: {
      candidate: "Candidate Code Name",
      source: "Source",
      ai_match: "AI Match, from the resume only",
      ready_pick_grade: "Vivekium Grade",
      ready_pick_note: "Vivekium Note",
      ready_pick_profile: "Vivekium Profile",
      team_review: "Team Review",
      stage: "Stage",
    },
    rows: [],
    total: 0,
    page: 1,
    page_size: 25,
    controls: controls(),
    source_types: ["applied", "sourced", "databank"],
    source_labels: {
      applied: "Applied",
      sourced: "Sourced",
      databank: "Databank",
    },
    ai_match_grades: [
      "Highly Matching",
      "Matching",
      "Moderately Matching",
      "Not Matching",
    ],
    stages: ["Applied", "Screening", "Shortlisted", "Interview", "Offer", "Closed"],
    sort_keys: ["grade", "name", "added", "source", "ai_match", "stage"],
    ...overrides,
  };
}

beforeEach(() => {
  apiGet.mockReset();
});
afterEach(cleanup);

describe("the candidate dashboard table", () => {
  it("renders the eight columns in the specified scanning order", async () => {
    apiGet.mockResolvedValue(page());
    render(<CandidateDashboard />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());

    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(8);
    // The order is the tab order and the decision order, both. Asserted as a
    // list rather than as a count, because eight columns in the wrong order is
    // still eight columns and breaks the triage read.
    expect(headers.map((h) => h.textContent)).toEqual([
      "CandidateCandidate Code Name",
      "SourceSource",
      "AI MatchAI Match, from the resume only",
      "Vivekium GradeVivekium Grade",
      "Vivekium NoteVivekium Note",
      "ProfileVivekium Profile",
      "Team ReviewTeam Review",
      "StageStage",
    ]);
  });

  it("names the first column 'Candidate Code Name' for a screen reader", () => {
    // The visible header is "Candidate". A blind reader arriving at a cell
    // holding a name AND a monospace code needs to know both are there.
    expect(page().column_labels.candidate).toBe("Candidate Code Name");
  });

  it("marks a flagged row with a border AND with words", async () => {
    // Colour is never the sole carrier of meaning. The border is the visual
    // cue; column 4 also reads "Under Review" and the stage control shows a
    // lock with a tooltip.
    apiGet.mockResolvedValue(
      page({
        rows: [
          row({
            under_integrity_review: true,
            ranking_state: STATE_UNDER_REVIEW,
            ranking_label: "Under Review",
            ranking_screen_reader_label:
              "Status: Under Review, awaiting integrity disposition",
          }),
        ],
        total: 1,
      })
    );
    render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByTestId("dashboard-row")).toBeTruthy());

    const tableRow = screen.getByTestId("dashboard-row");
    expect(tableRow.getAttribute("data-under-review")).toBe("true");
    expect(tableRow.className).toContain("border-l-4");
    expect(screen.getByText(/awaiting integrity disposition/)).toBeTruthy();
  });

  it("fades an archived row rather than hiding it", async () => {
    apiGet.mockResolvedValue(
      page({ rows: [row({ archived: true, stage: "Closed", stage_label: "Closed" })], total: 1 })
    );
    render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByTestId("dashboard-row")).toBeTruthy());
    expect(screen.getByTestId("dashboard-row").className).toContain("opacity-50");
  });

  it("offers every source the server knows about", async () => {
    // spec-doc6 C40: the Dashboard document lists two values and this
    // repository has three. A two-value filter silently hides every `sourced`
    // candidate, so the list comes from the server and is never hardcoded.
    apiGet.mockResolvedValue(page());
    render(<CandidateDashboard />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    for (const label of ["Applied", "Sourced", "Databank"]) {
      expect(screen.getByRole("option", { name: label })).toBeTruthy();
    }
  });

  it("asks the server to sort, and never sorts a fetched page itself", async () => {
    // Re-sorting one page in the browser lets a candidate appear on two pages,
    // or on none, as grades change. The request carries the sort.
    apiGet.mockResolvedValue(page());
    render(<CandidateDashboard />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(apiGet.mock.calls[0][0]).toContain("sort=grade");
    expect(apiGet.mock.calls[0][0]).toContain("direction=desc");
  });

  it("says so when the list cannot be loaded, and shows no rows", async () => {
    // NO SILENT FALLBACK. An empty table after a failed fetch is
    // indistinguishable from a job nobody has applied to.
    apiGet.mockRejectedValue(new Error("the service is unavailable"));
    render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("unavailable");
    expect(screen.queryByTestId("dashboard-row")).toBeNull();
  });

  it("tells a scoped viewer that they are seeing their assigned jobs", async () => {
    apiGet.mockResolvedValue(
      page({ controls: controls({ scoped_to_assignments: true }) })
    );
    render(<CandidateDashboard />);
    await waitFor(() =>
      expect(screen.getByText(/jobs you are assigned to/)).toBeTruthy()
    );
  });

  it("keeps horizontal scroll on the table and off the page", async () => {
    // The specification allows the table to scroll sideways and hides no
    // column by default. What must never scroll sideways is the page.
    apiGet.mockResolvedValue(page({ rows: [row()], total: 1 }));
    const { container } = render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByTestId("dashboard-row")).toBeTruthy());
    expect(container.querySelector(".overflow-x-auto")).toBeTruthy();
  });

  it("opens the jump-to-candidate field on '/'", async () => {
    apiGet.mockResolvedValue(page({ rows: [row()], total: 1 }));
    render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByTestId("dashboard-row")).toBeTruthy());

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "/" }));
    await waitFor(() =>
      expect(screen.getByLabelText(/Jump to candidate code/i)).toBeTruthy()
    );
  });

  it("says the code is not on this page rather than doing nothing", async () => {
    // A shortcut that silently no-ops is a shortcut people stop trusting.
    apiGet.mockResolvedValue(page({ rows: [row()], total: 1 }));
    render(<CandidateDashboard />);
    await waitFor(() => expect(screen.getByTestId("dashboard-row")).toBeTruthy());

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "/" }));
    const field = await screen.findByLabelText(/Jump to candidate code/i);
    fireEvent.change(field, { target: { value: "ZZZZ-ZZZZ-ZZZZ" } });
    fireEvent.click(screen.getByRole("button", { name: /Open profile/i }));
    await waitFor(() =>
      expect(screen.getByText(/No candidate on this page carries the code/)).toBeTruthy()
    );
  });

  it("filters AI Match by the four words the server serves", async () => {
    // The retired A / B / C / Hold letters are nowhere on the page: the
    // options are the server's grade words, and the request names the word.
    apiGet.mockResolvedValue(page());
    render(<CandidateDashboard />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    for (const label of ["Highly Matching", "Matching", "Moderately Matching", "Not Matching"]) {
      expect(screen.getByRole("option", { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole("option", { name: "Hold" })).toBeNull();

    const select = screen.getByLabelText(/AI Match/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "Matching" } });
    await waitFor(() =>
      expect(apiGet.mock.calls.at(-1)?.[0]).toContain("ai_match=Matching")
    );
  });

  it("shows a decisive grade for an assessed candidate and a status word otherwise", async () => {
    apiGet.mockResolvedValue(
      page({
        rows: [
          row({
            ai_match_state: STATE_HIGHLY,
            ai_match_label: "Highly Matching",
            ranking_state: STATE_HIGHLY,
            ranking_label: "Highly Matching",
            confidence_indicator: "filled",
            confidence_label: "High confidence",
            profile: { artifact: "ready_pick_profile", evaluation_id: "e1" },
            profile_pending_reason: null,
          }),
          row({ link_id: "second" }),
        ],
        total: 2,
      })
    );
    render(<CandidateDashboard />);
    await waitFor(() =>
      expect(screen.getAllByTestId("dashboard-row")).toHaveLength(2)
    );
    const states = screen
      .getAllByTestId("ready-pick-grade")
      .map((cell) => cell.getAttribute("data-state"));
    expect(states).toEqual([STATE_HIGHLY, STATE_NOT_CHECKED]);
  });

  it("renders no digit in any grade cell of any row (D3)", async () => {
    // The no-numbers rule on what a recruiter actually sees. Counts live in
    // the footer ("2 candidates", "Page 1 of 1"), never inside a row's
    // grade columns.
    apiGet.mockResolvedValue(
      page({
        rows: [
          row({
            ai_match_state: STATE_HIGHLY,
            ai_match_label: "Highly Matching",
            ranking_state: STATE_HIGHLY,
            ranking_label: "Highly Matching",
          }),
          row({ link_id: "second" }),
        ],
        total: 2,
      })
    );
    render(<CandidateDashboard />);
    await waitFor(() =>
      expect(screen.getAllByTestId("dashboard-row")).toHaveLength(2)
    );
    for (const id of ["ai-match", "ready-pick-grade"]) {
      for (const cell of screen.getAllByTestId(id)) {
        expect(cell.textContent ?? "").not.toMatch(/\d/);
      }
    }
  });
});

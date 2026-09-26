// @vitest-environment jsdom
//
// The job page's ranked candidate table (Vivekium release, PLAN-p2 WP-D).
//
// What is pinned here is what the TABLE owns: it renders the server's header
// sentence verbatim, it renders rows in the server's order (no client sort),
// the AI Match column is a word plus the tags the server flagged for the row
// plus a Details button, the not-assessed and not-yet-checked states say so,
// the applicant label sits under the procurement badge, and nothing the table
// renders in a row carries a percentage or a digit of its own making.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RankedCandidate, RankedCandidatesResponse } from "@/lib/types";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiFetch: vi.fn(),
  API_BASE: "http://api.test",
}));
// Stable across renders: the table lists `toast` in its fetch callback's
// dependencies, and a fresh function per render would refetch for ever.
const toastApi = vi.hoisted(() => ({ toast: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => toastApi }));
vi.mock("@/components/candidate-team-review-modal", () => ({
  CandidateTeamReviewModal: () => null,
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

import { CandidateRankingTable } from "./candidate-ranking-table";

const HEADER =
  "Assessed candidates are ranked mostly on their Tatva Assessment. Everyone else is a resume check only.";

function row(overrides: Partial<RankedCandidate>): RankedCandidate {
  return {
    link_id: "link",
    candidate_id: "cand",
    full_name: "Somebody",
    reference_code: "K7QP-2M4X-9TB1",
    email: null,
    source: null,
    archived_at: null,
    profile_id: "profile",
    has_resume: true,
    resume_filename: "resume.pdf",
    resume_mime_type: "application/pdf",
    has_report: false,
    report_ready_at: null,
    application_source: "direct",
    source_type: "applied",
    source_type_label: "Applied",
    applicant_label: null,
    profile_age: "new",
    profile_age_label: "New Profile",
    is_new_candidate: false,
    review_charged: false,
    status: "applied",
    stage_label: "Applied",
    status_updated_at: null,
    allowed_transitions: [],
    allowed_transition_options: [],
    validation_answers: [],
    prism_report_status: "Not available",
    proctoring_report_status: "Not available",
    // Server words with no digit in them, so the sweep below tests what the
    // TABLE adds. A real notice label ("Within 30 days") does carry digits:
    // it is the candidate's application answer, not a score, and it is the
    // server's text rendered as given.
    ctc_match_label: "Within range",
    notice_period_label: "Immediate",
    education_match_label: "Match",
    bgv_status: "not_started",
    bgv_status_label: "Not Started",
    ai_match_status: "pending",
    ai_match_label: null,
    ai_match_status_word: null,
    evidence_tags: [],
    provenance: [],
    ai_match_stale: false,
    ...overrides,
  } as RankedCandidate;
}

// Deliberately NOT in any order a client could compute: a pending row first,
// then the best grade, then a not-assessed row, then a stale legacy grade.
const ROWS: RankedCandidate[] = [
  row({
    link_id: "link-zed",
    full_name: "Zed Pending",
    status: "sourced",
    stage_label: "Sourced",
    source_type: "databank",
    source_type_label: "Databank",
    applicant_label: "Databank, not an applicant",
    ai_match_status: "pending",
    ai_match_status_word: "Not checked yet",
    provenance: ["AI Matching has not read this resume yet."],
  }),
  row({
    link_id: "link-asha",
    full_name: "Asha Scored",
    ai_match_status: "scored",
    ai_match_label: "Highly Matching",
    evidence_tags: [
      { text: "Python", polarity: "positive", shown_in_row: true },
      { text: "SQL", polarity: "positive", shown_in_row: true },
      { text: "Kafka", polarity: "positive", shown_in_row: true },
      { text: "Kubernetes", polarity: "negative", shown_in_row: true },
      { text: "Terraform", polarity: "negative", shown_in_row: false },
    ],
    provenance: [
      "Resume check: skills, experience and the role, read from the resume.",
      "Application answers: expected pay within range, available immediately.",
    ],
  }),
  row({
    link_id: "link-mira",
    full_name: "Mira Unassessed",
    ai_match_status: "not_assessed",
    ai_match_status_word: "Not assessed",
    provenance: ["No readable resume text."],
  }),
  row({
    link_id: "link-omar",
    full_name: "Omar Legacy",
    ai_match_status: "legacy",
    ai_match_label: "Moderately Matching",
    ai_match_stale: true,
    provenance: ["Checked before evidence tags existed. Run AI Matching to refresh."],
  }),
];

function page(overrides: Partial<RankedCandidatesResponse> = {}): RankedCandidatesResponse {
  return {
    job_id: "job-1",
    grade: "non_managerial",
    ranking_header: HEADER,
    results: ROWS,
    total: ROWS.length,
    page: 1,
    page_size: 25,
    total_pages: 1,
    has_next: false,
    has_previous: false,
    range_start: 1,
    range_end: ROWS.length,
    new_candidate_count: 0,
    ...overrides,
  };
}

/** Every text node inside the table body, minus the reference code, which is
 *  an identifier label (letters and digits by design), not a signal. */
function bodyText(container: HTMLElement): string {
  const body = container.querySelector("tbody");
  if (!body) throw new Error("the table has no body");
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement?.closest("[data-reference-code]")) continue;
    parts.push(node.textContent ?? "");
  }
  return parts.join(" ");
}

async function renderTable(response = page()) {
  api.apiGet.mockResolvedValue(response);
  const utils = render(
    <CandidateRankingTable
      jobId="job-1"
      onOpenReport={vi.fn()}
      onOpenTranscript={vi.fn()}
    />,
  );
  await screen.findByText("Asha Scored");
  return utils;
}

afterEach(cleanup);
beforeEach(() => {
  api.apiGet.mockReset();
  api.apiPost.mockReset();
  toastApi.toast.mockReset();
});

describe("CandidateRankingTable", () => {
  it("renders the server's header sentence verbatim", async () => {
    await renderTable();
    expect(screen.getByTestId("ranking-header").textContent).toBe(HEADER);
  });

  it("renders the resume-only sentence when that is what the server says", async () => {
    const sentence = "Resume check only. Real skills are tested in the assessment.";
    await renderTable(page({ ranking_header: sentence }));
    expect(screen.getByTestId("ranking-header").textContent).toBe(sentence);
  });

  it("keeps the server's row order and never sorts on the client", async () => {
    const { container } = await renderTable();
    const names = Array.from(container.querySelectorAll("tbody tr")).map(
      (tr) => tr.querySelector("td")?.textContent ?? "",
    );
    expect(names.map((name) => name.split("K7QP")[0].trim())).toEqual([
      "Zed Pending",
      "Asha Scored",
      "Mira Unassessed",
      "Omar Legacy",
    ]);
  });

  it("shows no percentage and no digit anywhere in the rows", async () => {
    const { container } = await renderTable();
    const text = bodyText(container);
    expect(text).not.toMatch(/%/);
    expect(text).not.toMatch(/\d/);
    // The retired columns are gone from the header too.
    const headers = Array.from(container.querySelectorAll("thead th")).map(
      (th) => (th.textContent ?? "").trim(),
    );
    expect(headers).toContain("AI Match");
    expect(headers).not.toContain("Match");
    expect(headers.join(" | ")).not.toMatch(/AI Rating|%/);
  });

  it("renders the grade word, the row's tags with screen reader prefixes, and points to Details for the rest", async () => {
    const { container } = await renderTable();
    const asha = screen.getByText("Asha Scored").closest("tr") as HTMLElement;
    const cell = within(asha).getByTestId("ai-match-cell");
    expect(within(cell).getByText("Highly Matching")).toBeTruthy();

    const tags = Array.from(cell.querySelectorAll("li"));
    expect(tags.map((tag) => tag.getAttribute("data-polarity"))).toEqual([
      "positive",
      "positive",
      "positive",
      "negative",
    ]);
    // The screen reader hears the polarity, not an icon.
    expect(tags[0].textContent).toBe("Evidenced: Python");
    expect(tags[3].textContent).toBe("Not evidenced: Kubernetes");
    expect(tags[0].querySelector(".sr-only")?.textContent?.trim()).toBe("Evidenced:");
    // The tag the server did not flag for the row is not in the row.
    expect(within(cell).queryByText("Terraform")).toBeNull();
    expect(within(cell).getByText("More in Details")).toBeTruthy();
    expect(container.querySelector("svg.lucide-check")).toBeTruthy();
  });

  it("says Not checked yet, Not assessed and Out of date in words", async () => {
    await renderTable();
    const zed = screen.getByText("Zed Pending").closest("tr") as HTMLElement;
    expect(within(zed).getByText("Not checked yet")).toBeTruthy();
    const mira = screen.getByText("Mira Unassessed").closest("tr") as HTMLElement;
    expect(within(mira).getByText("Not assessed")).toBeTruthy();
    expect(within(mira).queryByRole("list", { name: "Evidence tags" })).toBeNull();
    const omar = screen.getByText("Omar Legacy").closest("tr") as HTMLElement;
    expect(within(omar).getByText("Moderately Matching")).toBeTruthy();
    expect(within(omar).getByText("Out of date")).toBeTruthy();
  });

  it("renders the applicant label under the procurement badge", async () => {
    await renderTable();
    const zed = screen.getByText("Zed Pending").closest("tr") as HTMLElement;
    const label = within(zed).getByText("Databank, not an applicant");
    const badge = within(zed).getByText("Databank");
    expect(label.closest("td")).toBe(badge.closest("td"));
    const asha = screen.getByText("Asha Scored").closest("tr") as HTMLElement;
    expect(within(asha).queryByText(/not an applicant/)).toBeNull();
  });

  it("opens the AI Match details from the row, with every tag and the provenance", async () => {
    await renderTable();
    fireEvent.click(screen.getByRole("button", { name: "AI Match details for Asha Scored" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Terraform")).toBeTruthy();
    expect(
      within(dialog).getByText(
        "Resume check: skills, experience and the role, read from the resume.",
      ),
    ).toBeTruthy();
  });

  it("records the review of an Old Profile when its details are opened", async () => {
    const old = row({
      link_id: "link-old",
      full_name: "Asha Scored",
      profile_age: "old",
      profile_age_label: "Old Profile",
      ai_match_status: "scored",
      ai_match_label: "Matching",
    });
    api.apiPost.mockResolvedValue({ profile_age: "old", charged: true, subunits_charged: 3 });
    await renderTable(page({ results: [old], total: 1, range_end: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "AI Match details for Asha Scored" }));
    await waitFor(() =>
      expect(api.apiPost).toHaveBeenCalledWith("/jobs/job-1/candidates/link-old/review"),
    );
  });
});

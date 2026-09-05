// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { apiGet, toastApi } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  // Stable across renders: the real useToast returns a stable `toast`, and the
  // modal lists it in its fetch effect's dependency array. A mock minting a
  // fresh function every render re-runs that effect after each render, which
  // resets the loaded report and makes every "after the fetch" assertion racy.
  toastApi: { toast: vi.fn() },
}));

vi.mock("@/lib/api", () => ({ apiGet }));
vi.mock("@/components/ui/toast", () => ({
  useToast: () => toastApi,
}));
vi.mock("@/components/functional-skills-report", () => ({
  FunctionalSkillsReportView: () => <div>Rendered report</div>,
}));
// Mocked so these tests stay about the modal: the section has its own fetch
// (the video metadata route), which would otherwise consume the mocked apiGet.
vi.mock("@/components/assessment-video-section", () => ({
  AssessmentVideoSection: ({ linkId }: { linkId: string }) => (
    <div>Assessment video section for {linkId}</div>
  ),
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

import { PPIReportModal } from "./ppi-report-modal";

afterEach(() => {
  cleanup();
  apiGet.mockReset();
});

describe("PPIReportModal PDF export", () => {
  it("offers one-click download only after the immutable report is ready", async () => {
    apiGet.mockResolvedValue({
      id: "report-1",
      job_candidate_link_id: "link-1",
      reference_code: "K7QP-2M4X-9TB1",
      ai_score: [],
      primary_skills: [],
      secondary_skills: [],
      behavioural: [],
      technical: [],
      validation: {},
      suggested_interview_questions: [],
      radar_charts: [],
      overall_grade: "Matching",
      overall_summary: "Evidence summary",
      synthesized_at: "2026-08-07T00:00:00Z",
      report_download_allowed: true,
    });

    render(
      <PPIReportModal
        open
        onOpenChange={() => undefined}
        linkId="link-1"
        candidateName="Fixture Candidate"
        jobTitle="Platform Engineer"
      />
    );

    expect(screen.queryByRole("link", { name: /Download PDF/i })).toBeNull();
    const link = await screen.findByRole("link", { name: /Download PDF/i });
    expect(link.getAttribute("href")).toBe(
      "/api/v2/assessments/reports/links/link-1/pdf"
    );
    expect(link.hasAttribute("download")).toBe(true);
  });

  it("shows view-only instead of a dead Download button without retention consent", async () => {
    // Consent & Privacy spec (2026-09-05): the candidate chose "this job
    // only" (or was never asked), so the server answers 403 on the PDF route.
    // The UI must say why rather than offer a button that fails.
    apiGet.mockResolvedValue({
      id: "report-1",
      job_candidate_link_id: "link-1",
      reference_code: "K7QP-2M4X-9TB1",
      ai_score: [],
      must_have: [],
      nice_to_have: [],
      behavioural: [],
      validation: {},
      radar_charts: [],
      overall_grade: "Matching",
      overall_summary: "Evidence summary",
      synthesized_at: "2026-09-05T00:00:00Z",
      report_download_allowed: false,
    });

    render(
      <PPIReportModal
        open
        onOpenChange={() => undefined}
        linkId="link-1"
        candidateName="Fixture Candidate"
        jobTitle="Platform Engineer"
      />
    );

    expect(
      await screen.findByText(/View only, at the candidate's request/i)
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Download PDF/i })).toBeNull();
  });

  it("heads the document with the PRISM name and its expansion", async () => {
    // The abbreviation alone does not tell a reader that this is the DOCUMENT
    // and Tatva Assessment is the process that produced it, which is the one
    // confusion the client called out twice.
    apiGet.mockResolvedValue({
      id: "report-1",
      job_candidate_link_id: "link-1",
      reference_code: "K7QP-2M4X-9TB1",
      ai_score: [],
      must_have: [],
      nice_to_have: [],
      behavioural: [],
      validation: {},
      radar_charts: [],
      overall_grade: "Matching",
      overall_summary: "Evidence summary",
      synthesized_at: "2026-08-23T00:00:00Z",
    });

    render(
      <PPIReportModal
        open
        onOpenChange={() => undefined}
        linkId="link-1"
        candidateName="Fixture Candidate"
        jobTitle="Platform Engineer"
      />
    );

    expect(screen.getByRole("heading", { name: "PRISM Report" })).toBeTruthy();
    expect(
      screen.getByText("Predictive Role Intelligence & Suitability Mapping")
    ).toBeTruthy();
    expect(screen.getByText(/Fixture Candidate/)).toBeTruthy();
    // The code identifies a row on a printed page; it authorises nothing, so it
    // is rendered as plain text and nothing reads it back.
    // Generous timeout: the code appears only once the fetch resolves, and the
    // 1s default makes this assertion fail on a loaded machine rather than on a
    // real defect.
    expect(
      await screen.findByText("K7QP-2M4X-9TB1", {}, { timeout: 5000 })
    ).toBeTruthy();
  });

  it("carries the Assessment video section beside the report", async () => {
    // 2026-09-05 dashboard/video spec section 20: the Executive Profile
    // surface gains the video component BESIDE the PRISM Report, never as a
    // ninth report section (the report's section order is fixed and pinned).
    apiGet.mockResolvedValue({
      id: "report-1",
      job_candidate_link_id: "link-1",
      ai_score: [],
      must_have: [],
      nice_to_have: [],
      behavioural: [],
      validation: {},
      radar_charts: [],
      overall_grade: "Matching",
      overall_summary: "Evidence summary",
      synthesized_at: "2026-09-05T00:00:00Z",
    });

    render(
      <PPIReportModal
        open
        onOpenChange={() => undefined}
        linkId="link-1"
        candidateName="Fixture Candidate"
      />
    );

    expect(
      await screen.findByText("Assessment video section for link-1")
    ).toBeTruthy();
  });
});

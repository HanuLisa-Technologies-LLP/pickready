// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));

vi.mock("@/lib/api", () => ({ apiGet }));
vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));
vi.mock("@/components/functional-skills-report", () => ({
  FunctionalSkillsReportView: () => <div>Rendered report</div>,
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
});

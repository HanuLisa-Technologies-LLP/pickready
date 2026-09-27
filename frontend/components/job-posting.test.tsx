// @vitest-environment jsdom
//
// The job posting as a candidate reads it (CONTRACT v10; shapes in
// docs/release/2026-09-vivekium/s4-api-shapes.md). What is pinned:
//
// * a skill reaches a candidate as its NAME under its bucket's server label,
//   and nothing else: an entry carrying more still renders the name only;
// * a legacy job with no saved skills (`skill_buckets: []`, or an older
//   server sending nothing) renders no skills section, not empty headings;
// * the recruiter's Final Job Posting renders the server's preview: title,
//   band, the JD document, the three buckets and the company narrative, with
//   the grade said to be outside the posting.

import * as React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PostingPreview, PostingSkillBucket } from "@/lib/types";

const http = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", () => http);

import {
  FinalJobPostingPreview,
  PostingSkillsList,
  experienceBandText,
  postingSkillsFrom,
} from "./job-posting";

afterEach(cleanup);
beforeEach(() => http.apiGet.mockReset());

const BUCKETS: PostingSkillBucket[] = [
  { bucket: "must_have", label: "Must-have skills", names: ["Python", "SQL"] },
  { bucket: "nice_to_have", label: "Nice-to-have skills", names: ["Terraform"] },
  {
    bucket: "behavioural",
    label: "Behavioural competencies",
    names: ["Owns incidents to closure"],
  },
];

function preview(overrides: Partial<PostingPreview> = {}): PostingPreview {
  return {
    job_id: "job-1",
    title: "Backend Engineer",
    department: "Platform",
    grade: "managerial",
    grade_label: "Managerial",
    experience_band: "3 to 6 years",
    jd_markdown: "Build the ingestion services.\n\nOwn the event pipeline.",
    company_name: "Acrm Corp",
    about_company: "We build payroll software.",
    work_life: "Hybrid, three days in the office.",
    benefits: null,
    skill_buckets: BUCKETS,
    skills_saved: true,
    published: false,
    public_application_url: null,
    publish_blocked_reason: null,
    frozen: false,
    frozen_at: null,
    frozen_reason: null,
    ...overrides,
  };
}

describe("postingSkillsFrom", () => {
  it("keeps the server's label and the names, in posting order, skipping empty buckets", () => {
    expect(
      postingSkillsFrom([
        { bucket: "behavioural", label: "Behavioural competencies", names: ["Owns incidents"] },
        { bucket: "nice_to_have", label: "Nice-to-have skills", names: [] },
        { bucket: "must_have", label: "Must-have skills", names: [" Python ", "Python", "SQL"] },
      ])
    ).toEqual([
      { bucket: "must_have", label: "Must-have skills", names: ["Python", "SQL"] },
      { bucket: "behavioural", label: "Behavioural competencies", names: ["Owns incidents"] },
    ]);
  });

  it("keeps the names ONLY when an entry carries more", () => {
    const buckets = postingSkillsFrom([
      {
        bucket: "must_have",
        label: "Must-have skills",
        names: ["Python", { name: "Hidden", priority: 1 }],
        evidence_lines: ["Ships typed services."],
      },
    ]);
    expect(buckets).toEqual([
      { bucket: "must_have", label: "Must-have skills", names: ["Python"] },
    ]);
  });

  it("reads a legacy job without saved skills as null", () => {
    expect(postingSkillsFrom([])).toBeNull();
    expect(postingSkillsFrom(undefined)).toBeNull();
    expect(postingSkillsFrom(null)).toBeNull();
    expect(
      postingSkillsFrom([
        { bucket: "must_have", label: "Must-have skills", names: [] },
        { bucket: "nice_to_have", label: "Nice-to-have skills", names: [] },
        { bucket: "behavioural", label: "Behavioural competencies", names: [] },
      ])
    ).toBeNull();
    expect(postingSkillsFrom({ must_have: ["Python"] })).toBeNull();
  });
});

describe("PostingSkillsList", () => {
  it("shows each bucket under its label, names only", () => {
    render(<PostingSkillsList buckets={BUCKETS} />);
    const mustHave = screen.getByRole("region", { name: "Must-have skills" });
    expect(within(mustHave).getByText("Python")).toBeTruthy();
    expect(within(mustHave).getByText("SQL")).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Behavioural competencies" })).getByText(
        "Owns incidents to closure"
      )
    ).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(3);
  });

  it("renders nothing for a job with no skills", () => {
    const { container } = render(<PostingSkillsList buckets={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("uses the heading level the surrounding outline needs", () => {
    render(<PostingSkillsList buckets={BUCKETS} headingLevel={4} />);
    expect(screen.getAllByRole("heading", { level: 4 })).toHaveLength(3);
    expect(screen.queryAllByRole("heading", { level: 3 })).toHaveLength(0);
  });
});

describe("experienceBandText", () => {
  it("reads the band in the employer page's words", () => {
    expect(experienceBandText(3, 6)).toBe("3 to 6 years experience");
    expect(experienceBandText(2, null)).toBe("2+ years experience");
    expect(experienceBandText(null, 4)).toBe("Up to 4 years experience");
    expect(experienceBandText(null, undefined)).toBeNull();
  });
});

describe("FinalJobPostingPreview", () => {
  it("reads the server's preview and renders the posting a candidate reads", async () => {
    http.apiGet.mockResolvedValue(preview());
    render(<FinalJobPostingPreview jobId="job-1" />);

    const posting = await screen.findByRole("article", { name: "Posting preview" });
    expect(http.apiGet).toHaveBeenCalledWith(
      "/api/v2/assessments/jobs/job-1/posting-preview"
    );
    expect(within(posting).getByText("Backend Engineer")).toBeTruthy();
    expect(within(posting).getByText("Acrm Corp")).toBeTruthy();
    expect(within(posting).getByText("Platform · 3 to 6 years")).toBeTruthy();
    expect(within(posting).getByText(/Build the ingestion services\./)).toBeTruthy();
    expect(within(posting).getByText("Must-have skills")).toBeTruthy();
    expect(within(posting).getByText("Terraform")).toBeTruthy();
    expect(within(posting).getByText("About the company")).toBeTruthy();
    expect(within(posting).getByText("Hybrid, three days in the office.")).toBeTruthy();
    // An empty narrative section is not a heading over nothing.
    expect(within(posting).queryByText("Benefits")).toBeNull();
    expect(within(posting).queryByText(/not saved yet/)).toBeNull();
  });

  it("says the grade sets the assessment and keeps it OUT of the posting", async () => {
    http.apiGet.mockResolvedValue(preview());
    render(<FinalJobPostingPreview jobId="job-1" />);

    const posting = await screen.findByRole("article", { name: "Posting preview" });
    expect(
      screen.getByText(/It sets the assessment and is not shown on the posting\./)
    ).toBeTruthy();
    expect(within(posting).queryByText(/Managerial/)).toBeNull();
    expect(posting.textContent).not.toMatch(/Grade/);
  });

  it("says unsaved skills are unsaved", async () => {
    http.apiGet.mockResolvedValue(preview({ skills_saved: false }));
    render(<FinalJobPostingPreview jobId="job-1" />);
    expect(
      await screen.findByText(
        "These skills are not saved yet. Save them above before publishing."
      )
    ).toBeTruthy();
  });

  it("shows a job with no skills honestly, with no empty bucket headings", async () => {
    http.apiGet.mockResolvedValue(preview({ skill_buckets: [], skills_saved: false }));
    render(<FinalJobPostingPreview jobId="job-1" />);
    expect(
      await screen.findByText(/No skills yet\. The skills you add above appear here by name/)
    ).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Must-have skills" })).toBeNull();
  });

  it("re-reads when its key changes, and says a failed read with a retry", async () => {
    http.apiGet.mockRejectedValueOnce(new Error("Service unavailable"));
    const { rerender } = render(<FinalJobPostingPreview jobId="job-1" reloadKey="a" />);
    expect(await screen.findByText("The posting preview could not be loaded")).toBeTruthy();
    expect(screen.getByText("Service unavailable")).toBeTruthy();

    http.apiGet.mockResolvedValue(preview());
    rerender(<FinalJobPostingPreview jobId="job-1" reloadKey="b" />);
    await screen.findByRole("article", { name: "Posting preview" });
    await waitFor(() => expect(http.apiGet).toHaveBeenCalledTimes(2));
  });
});

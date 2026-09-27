// @vitest-environment jsdom
//
// The job posting as a candidate reads it (CONTRACT v10). What is pinned:
//
// * a skill reaches a candidate as its NAME under its bucket's heading, and
//   nothing else: a payload carrying an evidence line or a priority still
//   renders the name only;
// * a legacy job whose skills were never saved renders no skills section at
//   all, not three empty headings;
// * the recruiter's Final Job Posting shows the title, the band, the JD, the
//   three buckets and the company narrative, with the grade said to be
//   outside the posting.

import * as React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Job } from "@/lib/types";
import {
  FinalJobPostingPreview,
  POSTING_BUCKETS,
  PostingSkillsList,
  experienceBandText,
  postingSkillsFrom,
} from "./job-posting";

afterEach(cleanup);

const SKILLS = {
  must_have: ["Python", "SQL"],
  nice_to_have: ["Terraform"],
  behavioural: ["Owns incidents to closure"],
};

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    title: "Backend Engineer",
    department: "Platform",
    requirement_period: "",
    grade: "managerial",
    status: "draft",
    experience_min_years: 3,
    experience_max_years: 6,
    jd: {
      description: "Build the ingestion services.",
      responsibilities: ["Own the event pipeline", "Review designs"],
    },
    about_company: "We build payroll software.",
    work_life: "Hybrid, three days in the office.",
    benefits: null,
    ...overrides,
  } as unknown as Job;
}

describe("postingSkillsFrom", () => {
  it("keeps names by bucket, trimmed and without repeats", () => {
    expect(
      postingSkillsFrom({
        must_have: [" Python ", "Python", "SQL"],
        nice_to_have: [],
        behavioural: ["Owns incidents to closure"],
      })
    ).toEqual({
      must_have: ["Python", "SQL"],
      nice_to_have: [],
      behavioural: ["Owns incidents to closure"],
    });
  });

  it("keeps the name ONLY when an entry carries more", () => {
    const skills = postingSkillsFrom({
      must_have: [
        { id: "s1", name: "Python", priority: 1, evidence_line: "Ships typed services." },
      ],
    });
    expect(skills).toEqual({ must_have: ["Python"], nice_to_have: [], behavioural: [] });
  });

  it("reads a legacy job without skills as null", () => {
    expect(postingSkillsFrom(undefined)).toBeNull();
    expect(postingSkillsFrom(null)).toBeNull();
    expect(postingSkillsFrom({ must_have: [], nice_to_have: [], behavioural: [] })).toBeNull();
    expect(postingSkillsFrom(["Python"])).toBeNull();
    expect(postingSkillsFrom("Python")).toBeNull();
  });
});

describe("PostingSkillsList", () => {
  it("shows each bucket under the heading a candidate reads", () => {
    render(<PostingSkillsList skills={SKILLS} />);
    expect(POSTING_BUCKETS.map((bucket) => bucket.heading)).toEqual([
      "Must-have skills",
      "Nice-to-have skills",
      "Behavioural competencies",
    ]);
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

  it("omits an empty bucket and renders nothing for a job with no skills", () => {
    const { container, rerender } = render(
      <PostingSkillsList skills={{ ...SKILLS, nice_to_have: [] }} />
    );
    expect(screen.queryByText("Nice-to-have skills")).toBeNull();
    expect(screen.getByText("Must-have skills")).toBeTruthy();

    rerender(<PostingSkillsList skills={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("uses the heading level the surrounding outline needs", () => {
    render(<PostingSkillsList skills={SKILLS} headingLevel={4} />);
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
  it("renders the posting a candidate reads: title, band, JD, skills by name, narrative", () => {
    render(
      <FinalJobPostingPreview
        job={job()}
        skills={SKILLS}
        skillsSaved
        companyName="Acrm Corp"
      />
    );
    const posting = screen.getByRole("article", { name: "Posting preview" });
    expect(within(posting).getByText("Backend Engineer")).toBeTruthy();
    expect(within(posting).getByText("Acrm Corp")).toBeTruthy();
    expect(within(posting).getByText("Platform · 3 to 6 years experience")).toBeTruthy();
    expect(within(posting).getByText("Build the ingestion services.")).toBeTruthy();
    expect(within(posting).getByText("Own the event pipeline")).toBeTruthy();
    expect(within(posting).getByText("Must-have skills")).toBeTruthy();
    expect(within(posting).getByText("Terraform")).toBeTruthy();
    expect(within(posting).getByText("About the company")).toBeTruthy();
    expect(within(posting).getByText("Hybrid, three days in the office.")).toBeTruthy();
    // An empty narrative section is not a heading over nothing.
    expect(within(posting).queryByText("Benefits")).toBeNull();
    expect(within(posting).queryByText(/not saved yet/)).toBeNull();
  });

  it("says the grade sets the assessment and keeps it OUT of the posting", () => {
    render(<FinalJobPostingPreview job={job()} skills={SKILLS} skillsSaved />);
    expect(
      screen.getByText(/It sets the assessment and is not shown on the posting\./)
    ).toBeTruthy();
    const posting = screen.getByRole("article", { name: "Posting preview" });
    expect(within(posting).queryByText(/Managerial/)).toBeNull();
    expect(posting.textContent).not.toMatch(/Grade/);
  });

  it("says unsaved skills are unsaved", () => {
    render(<FinalJobPostingPreview job={job()} skills={SKILLS} skillsSaved={false} />);
    expect(
      screen.getByText("These skills are not saved yet. Save them above before publishing.")
    ).toBeTruthy();
  });

  it("shows a job with no skills honestly, with no empty bucket headings", () => {
    render(<FinalJobPostingPreview job={job()} skills={null} skillsSaved={false} />);
    expect(screen.getByText(/No skills yet\. The skills you add above appear here by name/)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Must-have skills" })).toBeNull();
  });
});

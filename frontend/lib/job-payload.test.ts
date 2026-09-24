import { describe, expect, it } from "vitest";

import { buildJobCreatePayload, type JobFormValues } from "./job-payload";

const JD = `## Description
Build reliable recruitment platform services.

## Role
Own backend systems

## Responsibilities
- Build reliable APIs
- Keep the pipeline green

## Skills
Python, FastAPI, PostgreSQL
`;

const completeForm: JobFormValues = {
  title: "Senior Backend Engineer",
  department: "Engineering",
  grade: "managerial",
  requirement_period: "Q4 2026",
  reporting_to: "Engineering Director",
  experience_min_years: "5",
  experience_max_years: "9",
  skills: "Python, FastAPI, PostgreSQL",
  jd_markdown: JD,
};

describe("buildJobCreatePayload", () => {
  it("creates a draft and carries no way to publish from the create call", () => {
    // Publishing is its own gated step (PUBLISH_JOB plus saved JD, SWOT and
    // skills). A create body that could still ask for it is how a job went
    // live with no skills and no index.
    const payload = buildJobCreatePayload(completeForm);
    expect(payload).not.toHaveProperty("publish");
  });

  it("sends the experience band and grade, never a free-text level", () => {
    const payload = buildJobCreatePayload(completeForm);
    expect(payload.experience_min_years).toBe(5);
    expect(payload.experience_max_years).toBe(9);
    expect(payload.grade).toBe("managerial");
    expect(payload).not.toHaveProperty("level");
  });

  it("carries the whole JD as one markdown document and derives no sections itself", () => {
    const payload = buildJobCreatePayload(completeForm);
    expect(payload.jd_markdown).toContain("## Responsibilities");
    // The server is the one parser. Only the two values that are NOT in the
    // document travel under `jd`.
    expect(Object.keys(payload.jd).sort()).toEqual(["reporting_to", "skills"]);
    expect(payload.jd.reporting_to).toBe("Engineering Director");
    expect(payload.jd.skills).toEqual(["Python", "FastAPI", "PostgreSQL"]);
  });

  it("sends the grade as the API's literal, not a trimmed display label", () => {
    expect(buildJobCreatePayload({ ...completeForm, grade: "cxo" }).grade).toBe("cxo");
  });

  it("blank experience and a blank document serialize to null", () => {
    const payload = buildJobCreatePayload({
      ...completeForm,
      experience_min_years: "",
      experience_max_years: "",
      jd_markdown: "   ",
    });
    expect(payload.experience_min_years).toBeNull();
    expect(payload.experience_max_years).toBeNull();
    expect(payload.jd_markdown).toBeNull();
  });
});

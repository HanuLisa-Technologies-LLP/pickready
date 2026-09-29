import { describe, expect, it } from "vitest";

import {
  buildJdGeneratePayload,
  buildJobCreatePayload,
  type JobFormValues,
  optionalNumber,
} from "./job-payload";

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
  department_id: "11111111-2222-4333-8444-555555555555",
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
    // The server is the one parser: no per-section `jd` travels, and the one
    // value the document does not carry is sent on its own.
    expect(payload).not.toHaveProperty("jd");
    expect(payload.reporting_to).toBe("Engineering Director");
    expect(buildJobCreatePayload({ ...completeForm, reporting_to: "  " }).reporting_to).toBeNull();
  });

  it("sends the department by id, never as free text", () => {
    // A Functional Head is confined to a department by its id, so the create
    // body names the picked row; the name only seeds the AI brief.
    const payload = buildJobCreatePayload(completeForm);
    expect(payload.department_id).toBe("11111111-2222-4333-8444-555555555555");
    expect(payload).not.toHaveProperty("department");
    expect(buildJobCreatePayload({ ...completeForm, department_id: "" }).department_id).toBeNull();
    expect(buildJdGeneratePayload(completeForm, "brief").department).toBe("Engineering");
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

describe("optionalNumber", () => {
  it("keeps a zero instead of turning it into null", () => {
    expect(optionalNumber("0")).toBe(0);
  });
  it("returns null only for a genuinely empty box", () => {
    expect(optionalNumber("")).toBeNull();
    expect(optionalNumber("   ")).toBeNull();
  });
  it("returns null rather than NaN for text that is not a number", () => {
    expect(optionalNumber("four")).toBeNull();
  });
});

describe("buildJdGeneratePayload", () => {
  it("sends a zero-year minimum and maximum as 0, not null", () => {
    const payload = buildJdGeneratePayload(
      { ...completeForm, experience_min_years: "0", experience_max_years: "0" },
      "a short brief",
    );
    expect(payload.experience_min_years).toBe(0);
    expect(payload.experience_max_years).toBe(0);
  });
  it("agrees with the create payload about the band", () => {
    const form = { ...completeForm, experience_min_years: "0", experience_max_years: "5" };
    const generate = buildJdGeneratePayload(form, "");
    const create = buildJobCreatePayload(form);
    expect(generate.experience_min_years).toBe(create.experience_min_years);
    expect(generate.experience_max_years).toBe(create.experience_max_years);
  });
});

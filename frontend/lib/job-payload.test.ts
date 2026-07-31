import { describe, expect, it } from "vitest";

import {
  buildJobCreatePayload,
  sectionsFromMarkdown,
  type JobFormValues,
} from "./job-payload";

const JD = `## Description
Build reliable recruitment platform services.

## Role
Own backend systems

## Responsibilities
- Build reliable APIs
- Keep the pipeline green

## Accountabilities
- Availability and delivery

## Education
B.Tech

## Skills
Python, FastAPI, PostgreSQL

## Experience
5 to 9 years
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

describe("sectionsFromMarkdown", () => {
  it("splits the one JD document into its headed sections", () => {
    const s = sectionsFromMarkdown(JD);
    expect(s["description"]).toBe("Build reliable recruitment platform services.");
    expect(s["role"]).toBe("Own backend systems");
    expect(s["education"]).toBe("B.Tech");
  });

  it("returns an empty map for a document with no headings", () => {
    expect(sectionsFromMarkdown("just some prose")).toEqual({});
  });
});

describe("buildJobCreatePayload", () => {
  it("sends the experience band the client asked for instead of a free-text level", () => {
    const payload = buildJobCreatePayload(completeForm);
    expect(payload.experience_min_years).toBe(5);
    expect(payload.experience_max_years).toBe(9);
    expect(payload).not.toHaveProperty("level");
  });

  it("carries the whole JD as one markdown document", () => {
    expect(buildJobCreatePayload(completeForm).jd_markdown).toContain("## Responsibilities");
  });

  it("derives the structured sections from that document so nothing downstream breaks", () => {
    const payload = buildJobCreatePayload(completeForm);
    expect(payload.jd.role).toBe("Own backend systems");
    // Bullet markers are stripped, one item per line.
    expect(payload.jd.responsibilities).toBe("Build reliable APIs\nKeep the pipeline green");
    expect(payload.jd.skills).toEqual(["Python", "FastAPI", "PostgreSQL"]);
  });

  it("no longer sends a reportee count, the field was removed from the product", () => {
    expect(buildJobCreatePayload(completeForm).jd).not.toHaveProperty("reportees");
  });

  it("creates as a draft by default and publishes only when asked", () => {
    expect(buildJobCreatePayload(completeForm).publish).toBe(false);
    expect(buildJobCreatePayload(completeForm, true).publish).toBe(true);
  });

  it("sends the grade as the API's literal, not a trimmed display label", () => {
    expect(buildJobCreatePayload(completeForm).grade).toBe("managerial");
    expect(buildJobCreatePayload({ ...completeForm, grade: "cxo" }).grade).toBe("cxo");
  });

  it("blank experience serializes to null rather than an invalid number", () => {
    const payload = buildJobCreatePayload({
      ...completeForm,
      experience_min_years: "",
      experience_max_years: "",
    });
    expect(payload.experience_min_years).toBeNull();
    expect(payload.experience_max_years).toBeNull();
  });
});

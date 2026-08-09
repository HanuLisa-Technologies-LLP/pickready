// Pins the questionnaire changes of 2026-08-09.
//
// The two properties asserted here are the ones that are expensive to get
// wrong: a retired id must never be reissued (it is the stored key in
// `aspects_json` and in every report written to date, so reusing it re-points
// historical answers at a different question), and what the CANDIDATE sees must
// still be a contiguous 1..N so the retirements do not surface as gaps.

import { describe, expect, it } from "vitest";

import { ASPECTS, aspectDisplayNo } from "./aspects";
import {
  OPTIONAL_ASPECT_IDS,
  REQUIRED_BOOLEAN_ASPECT_IDS,
  aspectProgress,
  missingAspects,
} from "@/components/aspects-form";

const RETIRED = [6, 7, 13, 29, 30, 31, 32, 33, 37, 38];

describe("candidate questionnaire", () => {
  it("no longer asks any retired question", () => {
    const ids = ASPECTS.map((a) => a.id);
    for (const id of RETIRED) expect(ids).not.toContain(id);
  });

  it("keeps every surviving id stable rather than renumbering into the gaps", () => {
    // Renumbering would be invisible in the UI and catastrophic in the data.
    expect(ASPECTS.map((a) => a.id)).toEqual([
      1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
      24, 25, 26, 27, 28, 34, 35, 36, 39, 40,
    ]);
  });

  it("shows the candidate a contiguous numbering with no gaps", () => {
    const shown = ASPECTS.map((a) => aspectDisplayNo(a.id));
    expect(shown).toEqual(ASPECTS.map((_, index) => index + 1));
  });

  it("asks about additional qualifications once, with the merged wording", () => {
    const merged = ASPECTS.filter((a) => /additional qualifications/i.test(a.question));
    expect(merged).toHaveLength(1);
    expect(merged[0].id).toBe(12);
    expect(merged[0].question).toBe(
      "Additional Qualifications (Professional certifications, Diplomas, Courses, Licenses)"
    );
  });

  it("asks for neither gender nor date of birth, which personal details covers", () => {
    expect(ASPECTS.some((a) => /gender/i.test(a.question))).toBe(false);
    expect(ASPECTS.some((a) => /date of birth/i.test(a.question))).toBe(false);
  });

  it("has no compensation section left on the questionnaire", () => {
    expect(ASPECTS.some((a) => a.category === "Compensation")).toBe(false);
    expect(ASPECTS.some((a) => /ctc/i.test(a.question))).toBe(false);
  });

  it("treats both consent questions as mandatory, not optional", () => {
    for (const id of REQUIRED_BOOLEAN_ASPECT_IDS) {
      expect(OPTIONAL_ASPECT_IDS).not.toContain(id);
      expect(missingAspects({}).map((a) => a.id)).toContain(id);
    }
  });

  it("counts an explicit No on a consent as answered, not as a refusal to answer", () => {
    // Mandatory means the candidate stated a position. It does not mean Yes.
    const declined = missingAspects({ 39: false, 40: false }).map((a) => a.id);
    expect(declined).not.toContain(39);
    expect(declined).not.toContain(40);
  });

  it("counts the consents in the progress meter", () => {
    const before = aspectProgress({});
    const after = aspectProgress({ 39: true, 40: false });
    expect(after.answered).toBe(before.answered + 2);
  });
});

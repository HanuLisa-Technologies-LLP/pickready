// The candidate questionnaire (PRD §7.5 / §7.6, numbering per API_CONTRACT.md rev 2).
// CONTRACT-FIXED slots (aspects_json keys, backend scoring reads these):
//   8–13 = education & qualifications (8 highest degree level, 9 specialization,
//          10 institution, 11 year of completion, 12 additional qualifications)
//   23   = current/most recent designation and core duties (title + responsibilities
//          in a single aspect)
//   40   = Databank matching consent (unchanged)
// ASSUMPTION: the surrounding aspects are not enumerated verbatim in the PRD;
// this is a defensible structured interpretation renumbered coherently around
// the contract-fixed slots.
//
// ── Retired ids (client decision, 2026-08-09) ──────────────────────────────
// `id` is the STORED key in `aspects_json` and in every report written to date,
// so a removed question's id is retired, never reissued and never used to close
// a gap by shifting its neighbours down. Renumbering 33 surviving questions
// would silently re-point every historical answer at a different question, and
// there is no migration that can undo that once a report has been read.
// `display_no` is what the candidate sees and it IS a contiguous 1..N,
// recomputed from the surviving list -- so the form has no gaps and the data has
// no collisions. Retired here: 6 (date of birth, superseded by the Age field in
// personal details), 7 (gender, asked in personal details already and therefore
// asked twice), 13 (merged into 12), 29-33 (the whole Compensation section: CTC
// and notice period are now mandatory fields on the application form itself and
// were being asked in both places), 37 and 38 (notice period in days and
// earliest joining date, same duplication).

export type AspectType = "text" | "number" | "select" | "boolean" | "date";

export interface AspectDef {
  id: number;
  category:
    | "Background"
    | "Education & Qualifications"
    | "Experience"
    | "Current Role"
    | "Compensation"
    | "Preferences"
    | "Availability"
    | "Verification & Consent";
  question: string;
  type: AspectType;
  options?: string[];
}

export const ASPECTS: AspectDef[] = [
  // ---- Background (1–7) ----
  { id: 1, category: "Background", question: "Full name (as per PF records / Class X memorandum)", type: "text" },
  { id: 2, category: "Background", question: "Current residing city", type: "text" },
  { id: 3, category: "Background", question: "Willing to relocate if the role requires it?", type: "boolean" },
  { id: 4, category: "Background", question: "Languages you can work in professionally", type: "text" },
  { id: 5, category: "Background", question: "Do you hold a valid passport?", type: "boolean" },
  // 6 (date of birth) and 7 (gender) retired 2026-08-09. Age and Gender are
  // collected in the personal-details section of the same page; asking gender
  // here as well put it on the form twice.

  // ---- Education & Qualifications (8–12, contract-fixed numbering) ----
  { id: 8, category: "Education & Qualifications", question: "Highest degree level attained", type: "select", options: ["Doctorate", "Master's", "Bachelor's", "Diploma", "Higher Secondary", "Other"] },
  { id: 9, category: "Education & Qualifications", question: "Specialization / major of your highest degree", type: "text" },
  { id: 10, category: "Education & Qualifications", question: "Institution / university of your highest degree", type: "text" },
  { id: 11, category: "Education & Qualifications", question: "Year of completion of your highest degree", type: "number" },
  // 12 absorbed 13 on 2026-08-09: certifications and the other qualifications
  // were two free-text boxes a candidate had to guess between.
  { id: 12, category: "Education & Qualifications", question: "Additional Qualifications (Professional certifications, Diplomas, Courses, Licenses)", type: "text" },

  // ---- Experience (14–22) ----
  { id: 14, category: "Experience", question: "Total years of professional experience", type: "number" },
  { id: 15, category: "Experience", question: "Years of experience relevant to the role applied for", type: "number" },
  { id: 16, category: "Experience", question: "Number of organizations worked at so far", type: "number" },
  { id: 17, category: "Experience", question: "Longest tenure at a single organization (years)", type: "number" },
  { id: 18, category: "Experience", question: "Any employment gaps of more than three months? If yes, explain.", type: "text" },
  { id: 19, category: "Experience", question: "Primary skills and tools you use day-to-day", type: "text" },
  { id: 20, category: "Experience", question: "Largest team size you have led or managed", type: "number" },
  { id: 21, category: "Experience", question: "Have you worked with distributed / remote teams?", type: "boolean" },
  { id: 22, category: "Experience", question: "Notable projects or achievements in your career so far", type: "text" },

  // ---- Current Role (23–28; 23 is contract-fixed) ----
  { id: 23, category: "Current Role", question: "Current / most recent designation and your core duties in that role", type: "text" },
  { id: 24, category: "Current Role", question: "Current / most recent employer name", type: "text" },
  { id: 25, category: "Current Role", question: "Date of joining current / most recent employer", type: "date" },
  { id: 26, category: "Current Role", question: "Current employment type", type: "select", options: ["Permanent", "Contract", "Consultant", "Self-employed", "Not currently employed"] },
  { id: 27, category: "Current Role", question: "Reason for looking for a change", type: "text" },
  { id: 28, category: "Current Role", question: "Are you currently under any bond or service agreement?", type: "boolean" },

  // ---- Compensation (29–33) retired 2026-08-09 ----
  // The whole section is gone. Current CTC and expected CTC are two of the
  // mandatory fields on the application form itself, so the candidate was
  // answering them twice on one page and a recruiter had two answers to the
  // same question with no rule for which one counted.

  // ---- Preferences (34–36) ----
  { id: 34, category: "Preferences", question: "Preferred work mode", type: "select", options: ["Onsite", "Hybrid", "Remote", "Flexible"] },
  { id: 35, category: "Preferences", question: "Preferred work locations (cities)", type: "text" },
  { id: 36, category: "Preferences", question: "Shift preference", type: "select", options: ["Day", "Night", "Rotational", "No preference"] },

  // ---- Availability (37–38) retired 2026-08-09 ----
  // Notice period is a mandatory field on the application form, and the
  // earliest joining date was removed from that form in the same change.

  // ---- Verification & Consent (39–40; 40 is contract-fixed) ----
  // Both are MANDATORY: a consent that renders "(optional)" is not a consent
  // anyone can rely on, and these two are the ones a recruiter acts on.
  { id: 39, category: "Verification & Consent", question: "Do you consent to background and previous-employer verification as part of this process?", type: "boolean" },
  { id: 40, category: "Verification & Consent", question: "Do you consent to your profile being retained in the ReadyPick Databank and matched against future roles?", type: "boolean" },
];

/**
 * What the candidate sees beside a question: a contiguous 1..N derived from the
 * surviving list, NOT the stored `id`. Retired ids leave gaps in `id` on
 * purpose (see the note at the top of this file); showing those gaps would tell
 * a candidate the form is broken, and closing them by editing `id` would
 * re-point every stored answer.
 */
const DISPLAY_NO = new Map<number, number>(
  ASPECTS.map((aspect, index) => [aspect.id, index + 1])
);

export function aspectDisplayNo(id: number): number | undefined {
  return DISPLAY_NO.get(id);
}

export const ASPECT_CATEGORIES = Array.from(
  new Set(ASPECTS.map((a) => a.category))
);

export function aspectById(id: number): AspectDef | undefined {
  return ASPECTS.find((a) => a.id === id);
}

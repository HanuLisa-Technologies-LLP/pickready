// The 40-aspect questionnaire (PRD §7.5 / §7.6, numbering per API_CONTRACT.md rev 2).
// CONTRACT-FIXED slots (aspects_json keys, backend scoring reads these):
//   8–13 = education & qualifications (8 highest degree level, 9 specialization,
//          10 institution, 11 year of completion, 12 professional certifications,
//          13 additional qualifications)
//   23   = current/most recent designation and core duties (title + responsibilities
//          in a single aspect)
//   40   = Databank matching consent (unchanged)
// ASSUMPTION: the surrounding aspects are not enumerated verbatim in the PRD;
// this is a defensible structured interpretation renumbered coherently around
// the contract-fixed slots, keeping 40 total.

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
  { id: 6, category: "Background", question: "Date of birth", type: "date" },
  { id: 7, category: "Background", question: "Gender", type: "select", options: ["Male", "Female", "Other", "Prefer not to say"] },

  // ---- Education & Qualifications (8–13, contract-fixed numbering) ----
  { id: 8, category: "Education & Qualifications", question: "Highest degree level attained", type: "select", options: ["Doctorate", "Master's", "Bachelor's", "Diploma", "Higher Secondary", "Other"] },
  { id: 9, category: "Education & Qualifications", question: "Specialization / major of your highest degree", type: "text" },
  { id: 10, category: "Education & Qualifications", question: "Institution / university of your highest degree", type: "text" },
  { id: 11, category: "Education & Qualifications", question: "Year of completion of your highest degree", type: "number" },
  { id: 12, category: "Education & Qualifications", question: "Professional certifications held (comma-separated)", type: "text" },
  { id: 13, category: "Education & Qualifications", question: "Additional qualifications (diplomas, courses, licenses)", type: "text" },

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

  // ---- Compensation (29–33) ----
  { id: 29, category: "Compensation", question: "Current annual CTC (INR)", type: "number" },
  { id: 30, category: "Compensation", question: "Current annual fixed / gross component (INR)", type: "number" },
  { id: 31, category: "Compensation", question: "Expected annual CTC (INR)", type: "number" },
  { id: 32, category: "Compensation", question: "Is your expected CTC negotiable?", type: "boolean" },
  { id: 33, category: "Compensation", question: "Do you have any pending variable pay / bonus / ESOP vesting you would forfeit?", type: "text" },

  // ---- Preferences (34–36) ----
  { id: 34, category: "Preferences", question: "Preferred work mode", type: "select", options: ["Onsite", "Hybrid", "Remote", "Flexible"] },
  { id: 35, category: "Preferences", question: "Preferred work locations (cities)", type: "text" },
  { id: 36, category: "Preferences", question: "Shift preference", type: "select", options: ["Day", "Night", "Rotational", "No preference"] },

  // ---- Availability (37–38) ----
  { id: 37, category: "Availability", question: "Official notice period at current employer (days)", type: "number" },
  { id: 38, category: "Availability", question: "Earliest date you can join (mention if buyout is available)", type: "text" },

  // ---- Verification & Consent (39–40; 40 is contract-fixed) ----
  { id: 39, category: "Verification & Consent", question: "Do you consent to background and previous-employer verification as part of this process?", type: "boolean" },
  { id: 40, category: "Verification & Consent", question: "Do you consent to your profile being retained in the PickReady Databank and matched against future roles?", type: "boolean" },
];

export const ASPECT_CATEGORIES = Array.from(
  new Set(ASPECTS.map((a) => a.category))
);

export function aspectById(id: number): AspectDef | undefined {
  return ASPECTS.find((a) => a.id === id);
}

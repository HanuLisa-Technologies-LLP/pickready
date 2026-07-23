// The 40-aspect questionnaire (PRD §7.5 / §7.6).
// ASSUMPTION: The PRD does not enumerate the 40 aspects verbatim; this list is a
// defensible structured interpretation covering background, experience,
// compensation, preferences, notice period, etc. Aspect 40 is the Databank
// matching consent (PRD Aspect 40 — governs Databank re-use).

export type AspectType = "text" | "number" | "select" | "boolean" | "date";

export interface AspectDef {
  id: number;
  category:
    | "Background"
    | "Education"
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
  // ---- Background (1–5) ----
  { id: 1, category: "Background", question: "Full name (as per PF records / Class X memorandum)", type: "text" },
  { id: 2, category: "Background", question: "Current residing city", type: "text" },
  { id: 3, category: "Background", question: "Willing to relocate if the role requires it?", type: "boolean" },
  { id: 4, category: "Background", question: "Languages you can work in professionally", type: "text" },
  { id: 5, category: "Background", question: "Do you hold a valid passport?", type: "boolean" },

  // ---- Education (6–10) ----
  { id: 6, category: "Education", question: "Highest educational qualification", type: "text" },
  { id: 7, category: "Education", question: "Specialization / major of highest qualification", type: "text" },
  { id: 8, category: "Education", question: "Year of completion of highest qualification", type: "number" },
  { id: 9, category: "Education", question: "Professional certifications held (comma-separated)", type: "text" },
  { id: 10, category: "Education", question: "Any education gaps of more than one year? If yes, explain.", type: "text" },

  // ---- Experience (11–18) ----
  { id: 11, category: "Experience", question: "Total years of professional experience", type: "number" },
  { id: 12, category: "Experience", question: "Years of experience relevant to the role applied for", type: "number" },
  { id: 13, category: "Experience", question: "Number of organizations worked at so far", type: "number" },
  { id: 14, category: "Experience", question: "Longest tenure at a single organization (years)", type: "number" },
  { id: 15, category: "Experience", question: "Any employment gaps of more than three months? If yes, explain.", type: "text" },
  { id: 16, category: "Experience", question: "Primary skills and tools you use day-to-day", type: "text" },
  { id: 17, category: "Experience", question: "Largest team size you have led or managed", type: "number" },
  { id: 18, category: "Experience", question: "Have you worked with distributed / remote teams?", type: "boolean" },

  // ---- Current Role (19–24) ----
  { id: 19, category: "Current Role", question: "Current employer name", type: "text" },
  { id: 20, category: "Current Role", question: "Current designation / title", type: "text" },
  { id: 21, category: "Current Role", question: "Date of joining current employer", type: "date" },
  { id: 22, category: "Current Role", question: "Current employment type", type: "select", options: ["Permanent", "Contract", "Consultant", "Self-employed", "Not currently employed"] },
  { id: 23, category: "Current Role", question: "Reason for looking for a change", type: "text" },
  { id: 24, category: "Current Role", question: "Are you currently under any bond or service agreement?", type: "boolean" },

  // ---- Compensation (25–29) ----
  { id: 25, category: "Compensation", question: "Current annual CTC (INR)", type: "number" },
  { id: 26, category: "Compensation", question: "Current annual fixed / gross component (INR)", type: "number" },
  { id: 27, category: "Compensation", question: "Expected annual CTC (INR)", type: "number" },
  { id: 28, category: "Compensation", question: "Is your expected CTC negotiable?", type: "boolean" },
  { id: 29, category: "Compensation", question: "Do you have any pending variable pay / bonus / ESOP vesting you would forfeit?", type: "text" },

  // ---- Preferences (30–34) ----
  { id: 30, category: "Preferences", question: "Preferred work mode", type: "select", options: ["Onsite", "Hybrid", "Remote", "Flexible"] },
  { id: 31, category: "Preferences", question: "Preferred work locations (cities)", type: "text" },
  { id: 32, category: "Preferences", question: "Willing to travel for work? Approximate percentage.", type: "text" },
  { id: 33, category: "Preferences", question: "Shift preference", type: "select", options: ["Day", "Night", "Rotational", "No preference"] },
  { id: 34, category: "Preferences", question: "Industries you prefer to work in", type: "text" },

  // ---- Availability (35–37) ----
  { id: 35, category: "Availability", question: "Official notice period at current employer (days)", type: "number" },
  { id: 36, category: "Availability", question: "Is your notice period negotiable / buyout available?", type: "boolean" },
  { id: 37, category: "Availability", question: "Earliest date you can join", type: "date" },

  // ---- Verification & Consent (38–40) ----
  { id: 38, category: "Verification & Consent", question: "Are there any pending or past disciplinary / legal proceedings involving you at any employer?", type: "text" },
  { id: 39, category: "Verification & Consent", question: "Do you consent to background and previous-employer verification as part of this process?", type: "boolean" },
  { id: 40, category: "Verification & Consent", question: "Do you consent to your profile being retained in the PickReady Databank and matched against future roles?", type: "boolean" },
];

export const ASPECT_CATEGORIES = Array.from(
  new Set(ASPECTS.map((a) => a.category))
);

export function aspectById(id: number): AspectDef | undefined {
  return ASPECTS.find((a) => a.id === id);
}

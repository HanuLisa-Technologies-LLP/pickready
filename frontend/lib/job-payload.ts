import type { JobGrade } from "./types";

export type JobFormValues = {
  title: string;
  department: string;
  /**
   * Assessment grade (spec §5/§6), REQUIRED by POST /jobs. Drives the
   * technical and PPI question counts, so it is sent as the
   * literal the API expects rather than a trimmed free-text value.
   */
  grade: JobGrade | "";
  requirement_period: string;
  /**
   * Who the role reports to. Chosen from the server's list
   * (GET /jobs/reporting-to-options) or typed freely after picking "Others".
   * Stored as whatever string the recruiter ended up with.
   */
  reporting_to: string;
  /**
   * The experience band, replacing the old free-text "Level" (client change,
   * 2026-07-28). Both are required and the server rejects min greater than max
   * with a CHECK constraint, so this is validated in one more place than the UI.
   */
  experience_min_years: string;
  experience_max_years: string;
  /** Comma-separated skills. Feeds both the AI brief and the JD itself. */
  skills: string;
  /**
   * The whole job description as ONE markdown document (client change,
   * 2026-07-28). It replaced seven separate text boxes: the AI drafts this,
   * the recruitment team edits it, and only then can the job be published.
   */
  jd_markdown: string;
};

const optionalNumber = (value: string): number | null => {
  const text = value.trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
};

export const skillsToArray = (value: string): string[] =>
  value
    .split(",")
    .map((skill) => skill.trim())
    .filter(Boolean);

/**
 * Pull the per-section strings the API still stores out of the one markdown
 * document. The server does the same parse authoritatively; this keeps the
 * created job's structured fields populated without asking the recruiter to
 * fill seven boxes again.
 */
export function sectionsFromMarkdown(markdown: string): Record<string, string> {
  const sections: Record<string, string> = {};
  let current = "";
  for (const rawLine of markdown.split("\n")) {
    const heading = rawLine.match(/^\s{0,3}#{1,6}\s+(.+?)\s*$/);
    if (heading) {
      current = heading[1].trim().toLowerCase();
      sections[current] = "";
      continue;
    }
    if (current) sections[current] += `${rawLine}\n`;
  }
  for (const key of Object.keys(sections)) sections[key] = sections[key].trim();
  return sections;
}

/** Strip list markers so a bullet block round-trips as clean lines. */
const asLines = (block: string | undefined): string =>
  (block ?? "")
    .split("\n")
    .map((line) => line.replace(/^\s*[-*+]\s+/, "").trim())
    .filter(Boolean)
    .join("\n");

export function buildJobCreatePayload(form: JobFormValues, publish = false) {
  const sections = sectionsFromMarkdown(form.jd_markdown);
  return {
    title: form.title.trim(),
    department: form.department.trim() || null,
    grade: form.grade,
    requirement_period: form.requirement_period.trim() || null,
    experience_min_years: optionalNumber(form.experience_min_years),
    experience_max_years: optionalNumber(form.experience_max_years),
    jd_markdown: form.jd_markdown.trim() || null,
    publish,
    jd: {
      description: sections["description"] || form.jd_markdown.trim() || null,
      reporting_to: form.reporting_to.trim() || null,
      role: sections["role"] || null,
      responsibilities: asLines(sections["responsibilities"]) || null,
      accountabilities: asLines(sections["accountabilities"]) || null,
      education: sections["education"] || null,
      skills: skillsToArray(form.skills),
      experience_years: optionalNumber(form.experience_min_years),
    },
  };
}

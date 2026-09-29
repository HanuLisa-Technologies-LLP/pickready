import type { JobGrade } from "./types";

export type JobFormValues = {
  title: string;
  /**
   * The department, chosen from the company's list (the leadership release,
   * spec 13): its id is what the server stores and what a Functional Head is
   * confined by, and its name rides along for the AI brief. A department is
   * picked or added through `components/department-picker.tsx`; nothing here
   * is free text any more.
   */
  department_id: string;
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

/**
 * A numeric form field as the API wants it: the number, or null when the box
 * is empty. Exported because the AI brief hand-rolled `Number(value) || null`,
 * and `0 || null` is null: a recruiter who typed 0 (legal, `ge=0`) sent
 * nothing and got a 422 under a toast blaming the AI. One coercion, used by
 * both payloads, is what stops that coming back (PR #5).
 */
export const optionalNumber = (value: string): number | null => {
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
 * The body of `POST /jobs`. It creates a DRAFT and nothing else.
 *
 * WHY THERE IS NO `publish` ARGUMENT ANY MORE (ReadyPick release, Phase 1)
 * ---------------------------------------------------------------------
 * This helper used to take `publish = true` from the Create Job screen, and
 * the server published the job inside the create call under `create_job`
 * alone. That skipped the real gate: `POST /jobs/{id}/publish` requires the
 * `publish_job` capability and a saved JD, a saved SWOT and saved skills, and
 * it is the only path that dispatches the JD indexing. A job went live with no
 * skills, a DRAFT lifecycle state and no index. Publishing is now a separate
 * step on the job page (`components/job-publish-card.tsx`), and the server
 * refuses `publish: true` on create loudly rather than ignoring it.
 *
 * WHY THE PER-SECTION `jd` FIELDS ARE GONE
 * ----------------------------------------
 * The markdown document is canonical and the server derives every section
 * from it, skills included. Deriving them here as well was a second parser
 * that could disagree with the first. The one value that is NOT in the
 * document, who the role reports to, travels as `reporting_to` and the server
 * stores it beside the derived sections. The skills the recruiter typed seed
 * the AI draft (`POST /jobs/generate-jd`) and reach the job only through the
 * document, so the candidate reads the same list the job is matched on.
 *
 * `level` is not sent and never was from this form; the experience band and
 * the grade replaced it.
 */
export function buildJobCreatePayload(form: JobFormValues) {
  return {
    title: form.title.trim(),
    department_id: form.department_id || null,
    grade: form.grade,
    requirement_period: form.requirement_period.trim() || null,
    experience_min_years: optionalNumber(form.experience_min_years),
    experience_max_years: optionalNumber(form.experience_max_years),
    jd_markdown: form.jd_markdown.trim() || null,
    reporting_to: form.reporting_to.trim() || null,
  };
}

/**
 * The Create-JD brief sent to `POST /jobs/generate-jd`. Lives beside
 * `buildJobCreatePayload` because the two carry the same experience band, and
 * when only one was built here the other drifted into a coercion that lost
 * zero. `brief` is the free-text Brief box; the API takes it as the
 * `key_requirements` alias it folds into `skills`.
 */
export function buildJdGeneratePayload(form: JobFormValues, brief: string) {
  return {
    title: form.title,
    department: form.department || null,
    grade: form.grade || null,
    skills: skillsToArray(form.skills),
    key_requirements: brief,
    reporting_to: form.reporting_to || null,
    experience_min_years: optionalNumber(form.experience_min_years),
    experience_max_years: optionalNumber(form.experience_max_years),
  };
}

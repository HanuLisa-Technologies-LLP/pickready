/**
 * The Candidate Dashboard's wire types.
 *
 * Kept in this folder rather than appended to `lib/types.ts` for one reason
 * that matters and one that does not. The one that matters: these mirror
 * `backend/app/schemas/dashboard.py` field for field, and a reader checking
 * that the two agree should have one file to open rather than a section of a
 * thousand-line one. The one that does not: `lib/types.ts` is edited by every
 * surface at once.
 *
 * THE ONE NUMBER
 * --------------
 * `ready_pick_score` is the only numeric assessment value in this product's
 * client-facing types, and it is deliberate. spec-doc6 D8 rules that the Ready
 * Pick Score renders on the dashboard and can never enter a delivered PRISM
 * Report; everything else here is a WORD the server chose. Nothing in this
 * folder computes a grade, a band or a label from a number: the server sends
 * `band`, `band_label`, `confidence_indicator` and every spoken label, so a
 * rendering layer cannot invent a verdict during an outage.
 */

/** Column 6's target. A DIFFERENT artefact from the PRISM Report. */
export interface ReadyPickProfileRef {
  artifact: "ready_pick_profile";
  evaluation_id: string;
}

/** One row, eight columns, in `dashboard.COLUMNS` order. */
export interface DashboardRow {
  link_id: string;
  candidate_id: string;
  full_name: string;
  /** COMPANY-JOB-CANDIDATE. A label; it authorises nothing. */
  system_id: string;
  job_id: string;
  job_title: string;

  source_type: string;
  source_label: string;

  /** A / B / C / Hold, or null when the resume has not been pre-screened. */
  pre_screen_grade: string | null;
  pre_screen_label: string;

  ready_pick_score: number | null;
  band: string;
  band_label: string;
  band_screen_reader_label: string;
  confidence: string | null;
  confidence_indicator: "filled" | "outline" | "grayed";
  confidence_label: string;
  score_range: string | null;
  score_range_note: string;

  note: string;
  note_is_pending: boolean;

  profile: ReadyPickProfileRef | null;
  profile_pending_reason: string | null;

  team_review_count: number;
  own_verdict: string | null;
  own_verdict_at: string | null;

  stage: string | null;
  stage_label: string;
  stage_on_hold: boolean;
  stored_status: string;

  under_integrity_review: boolean;
  archived: boolean;
}

/**
 * What this caller may do, resolved server-side.
 *
 * RBAC 3: frontend visibility is not a security boundary. These flags exist so
 * the UI does not offer a control the server would refuse, and every one of
 * them is enforced again at its own route.
 */
export interface DashboardControls {
  can_move_stage: boolean;
  stage_disabled_reason: string | null;
  can_team_review: boolean;
  team_review_disabled_reason: string | null;
  can_disposition_integrity: boolean;
  can_view_calibration: boolean;
  scoped_to_assignments: boolean;
}

export interface DashboardPage {
  columns: string[];
  column_labels: Record<string, string>;
  rows: DashboardRow[];
  total: number;
  page: number;
  page_size: number;
  controls: DashboardControls;
  /** Served, never hardcoded: the Source filter must not ship with two values
   *  while the database holds three (spec-doc6 C40). */
  source_types: string[];
  source_labels: Record<string, string>;
  pre_screen_grades: string[];
  stages: string[];
  sort_keys: string[];
}

export interface ProfileDimension {
  dimension: string;
  label: string;
  question: string;
  /** A NAMED rating. There is no score field and there must never be one. */
  rating: string | null;
  rated: boolean;
  insufficient_evidence: boolean;
  evidence_refs: string[];
}

export interface ReadyPickProfile {
  artifact: "ready_pick_profile";
  evaluation_id: string;
  candidate_name: string;
  system_id: string;
  why_this_candidate: string | null;
  dimensions: ProfileDimension[];
  category_ratings: Record<string, string>;
  overall_rating: string | null;
  capped_by_must_have: boolean;
  confidence: string | null;
  insufficient_dimensions: string[];
  authenticity_findings: unknown[];
  open_flags: Array<{ gate: string; blocking: boolean; reasons: string[] }>;
  under_integrity_review: boolean;
  needs_human_review: boolean;
  scorecard_version: number | null;
  company_dna_version: number | null;
  evaluated_at: string | null;
  scoring_mode: string | null;
}

export interface TeamReviewEntry {
  id: string;
  reviewer_user_id: string;
  reviewer_email: string | null;
  reviewer_role: string | null;
  verdict: string;
  verdict_label: string;
  remarks: string;
  created_at: string;
  updated_at: string;
  /** False for everybody but the author. RBAC 29. */
  editable: boolean;
}

export interface TeamReviewPanel {
  link_id: string;
  candidate_name: string;
  system_id: string;
  verdicts: string[];
  verdict_labels: Record<string, string>;
  entries: TeamReviewEntry[];
  can_write: boolean;
}

export interface StageOptions {
  stage: string | null;
  stage_label: string;
  stored_status: string;
  allowed_transitions: Array<{ status: string; label: string }>;
  can_move: boolean;
  disabled_reason: string | null;
}

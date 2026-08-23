// Types mirroring docs/API_CONTRACT.md responses.

export type Role =
  | "super_admin"
  | "client"
  | "recruitment_manager"
  | "hr_manager"
  | "recruiter"
  | "hiring_manager"
  | "candidate"
  // Business Development: ReadyPick's own sales staff. Platform staff, so
  // tenant_id is always null on this user.
  | "bd";

export interface User {
  id: string;
  role: Role;
  tenant_id: string | null;
  full_name: string;
  email: string;
  email_verified: boolean;
  phone_verified: boolean;
  workspace_name: string;
}

/** Capability strings resolved by the RBAC engine ("*" = owner/all). */
export type Capability = string;

/** Single-user auth success: cookies set, user + capabilities returned. */
export interface AuthSession {
  user: User;
  capabilities: Capability[];
}

/** One selectable workspace when an identifier matches multiple users. */
export interface AuthContextOption {
  user_id: string;
  role: Role;
  tenant_id: string | null;
  tenant_name: string | null;
  portal: "admin" | "org" | "portal" | string;
}

/** Multi-user auth: NO cookies yet, pick a context, then select-context. */
export interface AuthContextsResponse {
  contexts: AuthContextOption[];
  context_token: string;
}

export function isContextsResponse(
  res: AuthSession | AuthContextsResponse
): res is AuthContextsResponse {
  // The single-user response carries `contexts: null` (not an absent key), so
  // test the value, not key presence, otherwise every single-user login is
  // wrongly routed into the empty "choose workspace" step and never navigates.
  return Array.isArray((res as AuthContextsResponse).contexts)
    && (res as AuthContextsResponse).contexts.length > 0;
}

// ---- Provider Portal (the ReadyPick owner's view of its customers) ----
//
// A "customer" is one onboarded client company. It is the same underlying row
// the Owner console has always called a tenant, `Tenant` below stays for the
// onboarding and delete flows, while these types carry the customer-management
// view: analytics, lifecycle and compliance records.

export type CustomerStatus = "active" | "archived";

export type ComplianceDocumentType =
  | "gstin_certificate"
  | "pan_card"
  | "tan_number"
  | "bank_account_details"
  | "signed_agreement"
  | "purchase_order"
  | "msme_certificate";

export type ComplianceDocumentGroup = "tax" | "commercial";

/** The customer's HR Head. Read-only for the Provider, they maintain it. */
export interface PrimaryContact {
  user_id?: string | null;
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  /** Landline WITH extension, as one string. */
  landline?: string | null;
  status?: string | null;
}

/**
 * `jobs_closed` and `jobs_ongoing` OVERLAP during a job's 5-day grace period
 * and are not a partition of `jobs_posted`, they answer two independent
 * questions. Do not render them as parts of a whole.
 */
export interface CustomerAnalytics {
  jobs_posted: number;
  jobs_closed: number;
  jobs_ongoing: number;
  total_candidates_interacted: number;
  jobs_last_30_days: number;
}

export interface ComplianceDocument {
  id: string;
  document_type: ComplianceDocumentType;
  label: string;
  group: ComplianceDocumentGroup;
  file_name: string;
  mime_type?: string | null;
  size_bytes?: number | null;
  uploaded_at: string;
  uploaded_by_name?: string | null;
}

/** One of the seven slots. `document: null` renders "Not Available Yet". */
export interface ComplianceSlot {
  document_type: ComplianceDocumentType;
  label: string;
  group: ComplianceDocumentGroup;
  document: ComplianceDocument | null;
}

export interface Customer {
  id: string;
  name: string;
  industry?: string | null;
  website_domain?: string | null;
  /** The internal tenant key. Displayed as a subtitle, never editable. */
  domain: string;
  status: CustomerStatus;
  archived_at?: string | null;
  created_at: string;
  notes?: string | null;
  primary_contact: PrimaryContact;
  team_size: number;
  analytics: CustomerAnalytics;
}

export interface CustomerTeamMember {
  id: string;
  name?: string | null;
  email?: string | null;
  role: string;
  status: string;
}

export interface CustomerDetail extends Customer {
  culture?: string | null;
  details?: string | null;
  team: CustomerTeamMember[];
  compliance_documents: ComplianceSlot[];
}

export interface CustomerListResponse {
  customers: Customer[];
  total: number;
  page: number;
  page_size: number;
}

// ---- Admin ----

export interface Tenant {
  id: string;
  name: string;
  domain: string;
  spf_dkim_status?: string;
  industry?: string | null;
  culture?: string | null;
  details?: string | null;
  client_email?: string;
  client_phone?: string;
  client_name?: string | null;
  client_status?: string | null;
  staff_count?: number;
  created_at?: string;
}

export interface TenantProfile {
  id: string;
  name: string;
  industry?: string | null;
  culture?: string | null;
  details?: string | null;
  created_at: string;
  client_email?: string | null;
  client_name?: string | null;
  client_phone?: string | null;
  editable: boolean;
}

export interface PermissionEntry {
  role: string;
  capability: string;
  allowed: boolean;
}

export interface AuditLogEntry {
  id: string;
  tenant_id: string | null;
  actor_id?: string | null;
  actor_email?: string | null;
  action: string;
  detail?: string | Record<string, unknown> | null;
  created_at: string;
}

// ---- Company ----

/** Roles creatable through the staff page (contract rev 2). */
export type StaffRole =
  | "recruitment_manager"
  | "hr_manager"
  | "recruiter"
  | "hiring_manager";

/** Row from GET /companies/me/staff (contract rev 2). */
export interface StaffMember {
  id: string;
  email: string;
  full_name: string;
  phone?: string | null;
  role: StaffRole;
  status: string;
  approval_level?: string | null;
  created_at?: string | null;
  invite_status?: "pending" | "accepted" | "revoked" | "expired" | null;
  invite_sent_at?: string | null;
  invite_expires_at?: string | null;
  invite_link?: string | null;
  email_dispatch?: "queued" | "not_configured" | null;
}

/**
 * Row from GET /admin/bd-users, ReadyPick's own Business Development team.
 *
 * There is no tenant on this record and there never will be: a BD user is
 * platform staff. `signed_in` is false until Firebase binds an identity on the
 * person's first login, which is the usual reason a new account looks broken.
 */
export interface BDUser {
  id: string;
  email: string;
  full_name?: string | null;
  phone?: string | null;
  status: "invited" | "active" | "disabled" | string;
  created_at?: string | null;
  signed_in: boolean;
}

export type ApprovalLevelName =
  | "requested"
  | "recommended"
  | "approved"
  | "ratified";

export interface ApprovalLevelConfigEntry {
  active: boolean;
  approver_user_id: string | null;
}

export type ApprovalLevelsConfig = Record<
  ApprovalLevelName,
  ApprovalLevelConfigEntry
>;

export interface EmailTemplate {
  id?: string;
  name: string;
  subject: string;
  body: string;
}

// ---- Jobs ----

export interface JobJD {
  description: string;
  reporting_to: string;
  reportees: string | number;
  role: string;
  responsibilities: string | string[];
  accountabilities: string | string[];
  education: string;
  skills: string[];
  experience_years: number | string;
}

/**
 * Job grade (spec §5/§6). Drives the technical question count and the number
 * of PPI questions the candidate is asked (25/20/15/10). Required on
 * create; every job response carries it (never null).
 */
export type JobGrade = "non_managerial" | "managerial" | "leadership" | "cxo";

export const JOB_GRADES: { value: JobGrade; label: string }[] = [
  { value: "non_managerial", label: "Non-Managerial" },
  { value: "managerial", label: "Managerial" },
  { value: "leadership", label: "Leadership" },
  { value: "cxo", label: "CXO" },
];

export const jobGradeLabel = (grade?: JobGrade | string | null): string =>
  JOB_GRADES.find((g) => g.value === grade)?.label ?? "-";

export type JobStatus =
  | "draft"
  | "requested"
  | "recommended"
  | "approved"
  | "ratified"
  | "rejected";

export interface Job {
  id: string;
  title: string;
  department: string;
  /**
   * Legacy free-text seniority. Superseded 2026-07-28 by the experience band
   * below and no longer collected on the Create Job form, but still returned
   * for jobs created before that change.
   */
  level: string;
  /** The experience band this role expects, in years. */
  experience_min_years?: number | null;
  experience_max_years?: number | null;
  requirement_period: string;
  /**
   * The whole job description as one markdown document. Canonical since
   * 2026-07-28; the per-section fields on `jd` are derived from it.
   */
  jd_markdown?: string | null;
  /**
   * Absolute, shareable application link for this job. Present once the job is
   * published. This is the link a recruiter posts to LinkedIn or Naukri.
   */
  public_application_url?: string | null;
  /** Assessment grade (spec §5/§6), always present on a job response. */
  grade: JobGrade;
  status: JobStatus;
  jd: JobJD;
  compensation?: Record<string, unknown> | null;
  /**
   * Legacy/raw column names still emitted by the backend alongside the
   * canonical `jd` / `compensation` aliases. Read defensively via
   * `jobJd(job)` / `jobCompensation(job)` so the UI never silently degrades
   * to "-" when only one of the two names is present.
   */
  jd_json?: Partial<JobJD> | null;
  compensation_json?: Record<string, unknown> | null;
  created_at?: string;
  archived_at?: string | null;
  /**
   * Readiness of the auto-generated assessment. There is no recruiter-facing
   * question-bank UI, generation and finalization are fully automatic.
   */
  assessment_status?: "questions_pending_review" | "ready_for_candidates";
  /**
   * Company-narrative JD sections (spec §3.1). Already RESOLVED by the backend
   * through the per-job override -> company profile chain, so the UI renders
   * them directly and never has to know which layer supplied the text.
   */
  about_company?: string | null;
  work_life?: string | null;
  benefits?: string | null;
  /** Which of the three this job overrides, vs inherits from the company. */
  overridden_sections?: string[];
  public_url?: string | null;

  // ── Fixed 30-day posting window (spec §2.1) ────────────────────────────────
  // The recruiter never sets these. `posting_start_date` is stamped at publish;
  // the other two are database-generated and immutable.
  posting_start_date?: string | null;
  posting_end_date?: string | null;
  grace_period_end_date?: string | null;
  posting_status?: PostingStatus | null;
  days_until_posting_ends?: number | null;
  days_until_grace_ends?: number | null;
  posting_summary?: string | null;
}

/** Where a job sits in its fixed 30-day lifecycle. */
export type PostingStatus = "scheduled" | "active" | "grace_period" | "expired";

export const POSTING_STATUS_LABELS: Record<PostingStatus, string> = {
  scheduled: "Not yet live",
  active: "Live",
  grace_period: "Closed, grace period",
  expired: "Expired",
};

// ── The 10-stage hiring pipeline (spec §3.3) ─────────────────────────────────

export const PIPELINE_STATUSES = [
  "applied",
  "assessment_invited",
  "assessment_in_progress",
  "assessment_completed",
  "shortlisted",
  "interview_scheduled",
  "interview_completed",
  "offer_extended",
  "joined",
  "hold",
  "rejected",
] as const;
export type PipelineStage = (typeof PIPELINE_STATUSES)[number];

export const PIPELINE_LABELS: Record<PipelineStage, string> = {
  applied: "Application received",
  assessment_invited: "Assessment invitation sent",
  assessment_in_progress: "Assessment in progress",
  assessment_completed: "Assessment complete, under review",
  shortlisted: "Shortlisted",
  interview_scheduled: "Interview scheduled",
  interview_completed: "Interview complete",
  offer_extended: "Offer extended",
  joined: "Joined",
  hold: "On hold",
  rejected: "Not proceeding",
};

/** Short label for a table cell, where the full sentence is too wide. */
export const PIPELINE_SHORT_LABELS: Record<PipelineStage, string> = {
  applied: "Applied",
  assessment_invited: "Invited",
  assessment_in_progress: "Assessing",
  assessment_completed: "Assessed",
  shortlisted: "Shortlisted",
  interview_scheduled: "Interview set",
  interview_completed: "Interviewed",
  offer_extended: "Offer out",
  joined: "Joined",
  hold: "On hold",
  rejected: "Rejected",
};

export interface StatusEvent {
  status: string;
  label: string;
  at: string;
}

export interface PipelineStageCount {
  status: PipelineStage;
  label: string;
  count: number;
}

export interface CandidatePipeline {
  job_id: string;
  stages: PipelineStageCount[];
  total: number;
}

export interface ApplicationStatusResponse {
  link_id: string;
  status: PipelineStage;
  stage_label: string;
  status_updated_at?: string | null;
  allowed_transitions: PipelineStage[];
  timeline: StatusEvent[];
  email_queued?: boolean;
}

// ── The one rating scale (spec §10.2) ────────────────────────────────────────
// FOUR grades, and only four. They replaced the product's two parallel
// five-label scales on 2026-07-30: the matching scale that labelled the ranking
// comments, and the assessment scale that labelled report dimensions. Those had
// to be kept in step by hand, and a reader had no way to know that a "High" and
// a "Matching" meant the same thing.
//
// Numbers never appear on it. The backend (services/rating.py) converts its
// internal score to a word and only the word crosses the API boundary.

export const RATING_GRADES = [
  "Highly Matching",
  "Matching",
  "Moderately Matching",
  "Not Matching",
] as const;
export type RatingGrade = (typeof RATING_GRADES)[number];

/** @deprecated Use RATING_GRADES. Kept so older imports keep compiling. */
export const MATCHING_LABELS = RATING_GRADES;
export type MatchingLabel = RatingGrade;

export type RatingWordLabel = RatingGrade;

/** One row of the job page's inline candidate table. Carries no numbers. */
/** The three ways a candidate reaches a job. See `RankedCandidate.source_type`. */
export type CandidateProcurement = "applied" | "sourced" | "databank";

/** One legal pipeline move, with the label the server wants shown for it. */
export interface TransitionOption {
  status: PipelineStage;
  label: string;
}

/**
 * Old Profiles vs New Profiles, derived server-side from the application date
 * against the job's CURRENT posting window. A job that was never renewed has
 * only New Profiles.
 */
export type ProfileAge = "old" | "new";

export interface ReviewProfileResponse {
  profile_age: ProfileAge;
  /** False for a New Profile and for a re-open, both of which are free. */
  charged: boolean;
  subunits_charged: number;
}

/** One mandatory application field, or one candidate profile-form item, and
 *  the candidate's exact answer. */
export interface ValidationAnswer {
  key: string;
  question: string;
  /** Exactly as submitted. Null when this application predates the field or the
   *  candidate left it blank; the row still renders, saying "Not answered",
   *  because "never asked" and "did not answer" look identical when a row is
   *  simply missing and only one of them is the candidate's doing. */
  answer: string | null;
  /** "Application" for the six mandatory fields, or the profile form's own
   *  section title (e.g. "Work Experience") for the 38 profile items. Used
   *  only to group the modal into readable sections. */
  group?: string;
}

/** One matching category this candidate was ACTUALLY scored on. */
export interface MatchingCategoryResult {
  key: string;
  name: string;
  comment: string | null;
  label: RatingGrade | null;
}

export interface RankedCandidate {
  link_id: string;
  candidate_id: string;
  full_name: string;
  /**
   * COMPANY-JOB-CANDIDATE, e.g. "K7QP-2M4X-9TB1". Rendered under the name.
   * One readable handle for this application, stable everywhere it appears.
   * Derived server-side and one-way; it identifies a row without disclosing
   * anything about it, and it is never an authorisation input.
   */
  reference_code?: string;
  email?: string | null;
  /** The job's grade as a display label ("Non-managerial", "CXO", ...). */
  level: string;
  source?: CandidateSource | null;
  tier?: Tier | null;
  archived_at?: string | null;
  /** The application's Profile. Resumes live in private storage, so this is
   *  the handle the viewer and the download endpoint are keyed on. */
  profile_id?: string | null;
  resume_url?: string | null;
  resume_filename?: string | null;
  resume_mime_type?: string | null;
  has_report: boolean;
  report_ready_at?: string | null;
  /** Where this applicant came from (spec §1.1). */
  application_source?: "direct" | "sourced" | null;
  /** How this candidate was procured. Applied means they came through
   *  ReadyPick themselves, sourced means a third-party link, databank means
   *  the recruitment team uploaded them in bulk. All three are parsed,
   *  matched and assessed identically; this is display and filtering only. */
  source_type: CandidateProcurement;
  /** Server-rendered display text for `source_type`. */
  source_type_label: string;
  /** `old` when this application arrived BEFORE the job's current 30-day
   *  posting window, i.e. the job has since been renewed. Presentation and
   *  billing only: an Old Profile is ranked, listed and openable exactly like
   *  a new one. */
  profile_age: ProfileAge;
  /** "Old Profile" / "New Profile", so the UI never renders a raw enum. */
  profile_age_label: string;
  /** True once someone on the team has already paid the bulk review rate for
   *  this profile, so reopening it costs nothing. */
  review_charged: boolean;
  status: PipelineStage;
  stage_label: string;
  status_updated_at?: string | null;
  /** Legal moves from here, the UI renders exactly these, so no button can
   *  appear that would 409. */
  allowed_transitions: PipelineStage[];
  /** The same legal moves, each with the label to show. Prefer this over
   *  `allowed_transitions`: the labels come from the server, so the UI never
   *  has to hardcode a stage name it might get wrong. */
  allowed_transition_options: TransitionOption[];
  ranking_status: "not_scored" | "ready";
  skills_match_comment?: string | null;
  experience_comment?: string | null;
  role_alignment_comment?: string | null;
  education_comment?: string | null;
  overall_comment?: string | null;
  skills_match_label?: MatchingLabel | null;
  experience_label?: MatchingLabel | null;
  role_alignment_label?: MatchingLabel | null;
  education_label?: MatchingLabel | null;
  overall_label?: MatchingLabel | null;
  validation_answers: ValidationAnswer[];
}

export interface RankedCandidatesResponse {
  job_id: string;
  grade: JobGrade;
  level: string;
  results: RankedCandidate[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
  range_start: number;
  range_end: number;
}

export type TeamRating =
  | "very_high"
  | "high"
  | "medium"
  | "low"
  | "developing";

export interface CandidateTeamReview {
  id: string;
  reviewer_user_id: string;
  reviewer_name: string;
  rating: TeamRating;
  remarks: string;
  ai_rewritten_remarks: string | null;
  is_current_user: boolean;
  created_at: string;
  updated_at: string;
}

export interface CandidateTeamReviews {
  reviews: CandidateTeamReview[];
  overall_rating: TeamRating | null;
  overall_remarks: string | null;
  review_count: number;
}

export interface TeamReviewRewrite {
  rewritten_remarks: string;
  used_ai: boolean;
}

/** Company Portal -> Profile (spec §3.2). */
export interface CompanyProfile {
  tenant_id: string;
  company_name: string;
  industry?: string | null;
  about_company?: string | null;
  work_life?: string | null;
  benefits?: string | null;
  recommended_min_chars: number;
  recommended_max_chars: number;
}

// ── Lifecycle emails (spec §6) ───────────────────────────────────────────────

export const EMAIL_TYPES = [
  "application_confirmation",
  "assessment_reminder",
  "shortlist",
  "rejected",
  "hold",
  "question_bank_reminder",
] as const;
export type EmailType = (typeof EMAIL_TYPES)[number];

export const EMAIL_TYPE_LABELS: Record<EmailType, string> = {
  application_confirmation: "Application received",
  assessment_reminder: "Assessment reminder",
  shortlist: "Shortlisted, moving forward",
  rejected: "Not proceeding",
  hold: "On hold",
  question_bank_reminder: "Internal: job needs review",
};

export interface EmailDraft {
  link_id: string;
  candidate_id?: string | null;
  recipient_email?: string | null;
  candidate_name?: string | null;
  email_type: string;
  subject: string;
  body: string;
  /** False when a provider outage forced the deterministic template. */
  generated_by_ai: boolean;
}

export interface EmailDraftsResponse {
  email_type: string;
  drafts: EmailDraft[];
  skipped: { link_id: string; name?: string; reason: string }[];
}

export interface EmailLogEntry {
  id: string;
  email_type: string;
  recipient_email: string;
  subject: string;
  body: string;
  status: "queued" | "sent" | "failed";
  error?: string | null;
  edited_by_human: boolean;
  generated_by_ai: boolean;
  created_at: string;
  sent_at?: string | null;
}

/** Per-user permission matrix (spec §7.1). */
export interface StaffPermissions {
  user_id: string;
  role: Role;
  full_name?: string | null;
  email?: string | null;
  all_capabilities: Capability[];
  /** Granted by the ROLE, before any per-user pin. */
  role_defaults: Capability[];
  /** Explicit per-user pins. Sparse, absent means "follow the role". */
  overrides: Record<string, boolean>;
  /** What actually applies: role defaults with the overlay on top. */
  effective: Capability[];
  role_label?: string | null;
  /** Capabilities the current manager is allowed to grant. */
  grantable: Capability[];
}

export interface CompanyProfileResearch {
  about_company: string;
  work_life: string;
  benefits: string;
  sources: string[];
  degraded: boolean;
  message?: string | null;
}

/** The JD, whichever field name the backend used. Never undefined. */
export const jobJd = (job: Job | null | undefined): Partial<JobJD> =>
  (job?.jd ?? job?.jd_json ?? {}) as Partial<JobJD>;

/** The compensation object, whichever field name the backend used. */
export const jobCompensation = (
  job: Job | null | undefined
): Record<string, unknown> =>
  (job?.compensation ?? job?.compensation_json ?? {}) as Record<string, unknown>;

export interface ApprovalTransition {
  id?: string;
  level: string;
  decision?: "approved" | "rejected" | "skipped" | string;
  actor?: string | null;
  actor_name?: string | null;
  remarks?: string | null;
  created_at?: string;
  skipped?: boolean;
}

// ---- Candidates & matching ----

export type Tier =
  | "highly_matching"
  | "moderately_matching"
  | "matching"
  | "not_matching";

export type CandidateSource = "fresh" | "databank";

export type PipelineStatus =
  | "rejected"
  | "shortlisted"
  | "hold"
  | "offered"
  | "joined"
  | "pending"
  | string;

export interface CandidateSummary {
  id: string;
  full_name: string;
  email: string;
  phone?: string | null;
}

/** Client-safe projection of one stored ranking dimension. */
export interface MatchComment {
  comment: string;
}

/**
 * Comments-only API projection. Numeric ranking values remain server-side.
 */
export interface MatchBreakdown {
  skills_match?: MatchComment;
  experience_relevance?: MatchComment;
  role_alignment?: MatchComment;
  education_fit?: MatchComment;
  overall?: MatchComment;
  scoring_mode?: string;
}

export interface CandidateLink {
  link_id: string;
  candidate: CandidateSummary;
  source: CandidateSource;
  tier?: Tier | null;
  status?: PipelineStatus | null;
  current_status?: PipelineStatus | null;  // backend LinkOut field name
  status_remarks?: string | null;
  hm_access_granted?: boolean;
  rationale?: string | null;
  profile_id?: string | null;
  breakdown?: MatchBreakdown | null;
  ranking_status?: "not_scored" | "ready";
  skills_match_comment?: string | null;
  experience_comment?: string | null;
  role_alignment_comment?: string | null;
  education_comment?: string | null;
  overall_comment?: string | null;
  archived_at?: string | null;
}

export interface MatchingResult {
  link_id: string;
  candidate: CandidateSummary;
  source: CandidateSource;
  tier: Tier;
  rationale?: string | null;
  breakdown?: MatchBreakdown | null;
}

export interface AspectResponse {
  aspect_id: number;
  question?: string;
  answer: string | number | boolean | null;
}

export interface VerificationRequest {
  id: string;
  employer_email: string;
  status: string;
  designation?: string | null;
  doj?: string | null;
  doe?: string | null;
  last_drawn_ctc?: string | null;
  last_drawn_gross?: string | null;
  noc_status?: string | null;
  exit_formalities_complete?: boolean | null;
  bgv_status?: string | null;
  proofs_details?: string | null;
  prior_experience_details?: string | null;
  overridden?: boolean;
  override_reason?: string | null;
}

export interface CandidateProfile {
  id?: string;
  candidate: CandidateSummary;
  profile_id?: string;
  resume_fields?: {
    skills?: string[];
    experience?: unknown;
    education?: unknown;
    employment_history?: unknown;
    [key: string]: unknown;
  } | null;
  personal?: {
    full_name?: string;
    residing_city?: string;
    age?: number;
    gender?: string;
  } | null;
  aspects?: AspectResponse[] | null;
  verification?: VerificationRequest[] | null;
  resume_url?: string | null;
  resume_original_filename?: string | null;
  resume_mime_type?: string | null;
  parsed_fields_json?: CandidateProfile["resume_fields"];
  aspects_json?: Record<string, string | number | boolean | null> | null;
  verification_requests?: VerificationRequest[] | null;
}

// ---- Portal ----

export interface OutreachRequestInfo {
  candidate_name?: string;
  job_title?: string;
  tenant_name?: string;
  fields_requested?: string[];
  aspects?: { id: number; question: string }[];
}

export interface PortalJob {
  id: string;
  title: string;
  department?: string;
  level?: string;
  tenant_name?: string;
  company_name?: string;
  /** Present on the single-job read; the list endpoint may omit it. */
  jd?: Record<string, unknown> | null;
  jd_json?: Record<string, unknown> | null;
  /** Assessment grade, drives how many technical questions are asked. */
  grade?: string | null;
  // The employer, as the candidate needs to see it before applying.
  company_about?: string | null;
  company_culture?: string | null;
  company_industry?: string | null;
  company_benefits?: string | null;
}

export interface PortalApplication {
  id: string;
  job_id?: string;
  job_title: string;
  company_name?: string;
  status: string;
  updated_at?: string;
}

// ---- Verification form (public) ----

export interface VerificationFormInfo {
  candidate_name: string;
  fields?: string[];
}

export interface VerificationFormSubmission {
  designation: string;
  doj: string;
  doe: string;
  last_drawn_ctc: string;
  last_drawn_gross: string;
  noc_status: string;
  exit_formalities_complete: boolean;
  bgv_status: string;
  proofs_details: string;
  prior_experience_details: string;
}

// ---- Dashboard ----

export interface DashboardJobMetrics {
  job_id: string;
  title: string;
  databank_matched: number;
  fresh_sourced: number;
  shortlisted: number;
  offered: number;
  joined: number;
}

export interface DashboardSummary {
  jobs: DashboardJobMetrics[];
  total_jobs_worked: number;
}

// ---- AI Dashboard: REMOVED (spec 30) ----
// `AIGradeCount`, `AIAssessmentFunnel`, `AIFrameworkHealth` and `AIDashboard`
// lived here. The feature was removed from the customer portal entirely: the
// page, the component, the route and its response schemas all went in the same
// change, so these types described a payload nothing sends.

// ---- Billing, subscriptions and credits (killer-spec Parts 2 and 3) ----

/**
 * Credits are exchanged in SUB-UNITS everywhere except display. One credit is
 * 60 sub-units, so 1/3, 1/15 and 1/20 of a credit are all whole numbers and no
 * arithmetic on this side of the wire ever touches a float. The server also
 * sends `*_credits` already rounded to two decimals: render those, compute with
 * the sub-units.
 */
export const SUBUNITS_PER_CREDIT = 60;

export type SubscriptionStatus = "active" | "past_due" | "cancelled" | "halted";

export type CreditEventType =
  | "grant"
  | "completed_assessment"
  | "incomplete_assessment"
  | "no_show"
  | "old_profile_review"
  | "adjustment";

export interface PricingPlan {
  id: string;
  slug: string;
  name: string;
  applications_per_month: number;
  price_inr: number;
  rate_per_application_inr: number;
  is_active: boolean;
  /** False until a Razorpay Plan exists; Subscribe is disabled rather than failing. */
  checkout_ready: boolean;
}

export interface BillingConfig {
  razorpay_key_id: string | null;
  configured: boolean;
  currency: "INR";
  plans: PricingPlan[];
}

export interface SubscribeResponse {
  subscription_id: string;
  razorpay_key_id: string;
  plan: PricingPlan;
  short_url: string | null;
}

export interface SubscriptionSummary {
  plan: PricingPlan | null;
  status: SubscriptionStatus | null;
  razorpay_subscription_id: string | null;
  current_end: string | null;
}

export interface UsageBreakdown {
  completed_assessment: number;
  incomplete_assessment: number;
  no_show: number;
  old_profile_review: number;
  adjustment: number;
}

export interface CreditSummary {
  balance_subunits: number;
  balance_credits: string;
  balance_inr: string | null;
  subunits_per_credit: number;
  granted_subunits: number;
  consumed_subunits: number;
  rollover_subunits: number;
  rollover_credits: string;
  usage_this_month_subunits: UsageBreakdown;
  in_deficit: boolean;
  deficit_message: string | null;
  exhausted: boolean;
  low_balance: boolean;
  balance_fraction: number;
  low_balance_threshold: number;
  alert_message: string | null;
  unlimited: boolean;
}

export interface CreditLedgerEntry {
  id: string;
  event_type: CreditEventType;
  subunits_delta: number;
  credits_delta: string;
  created_at: string;
  job_candidate_link_id: string | null;
}

export interface BillingTransaction {
  id: string;
  razorpay_payment_id: string | null;
  amount_inr: number;
  status: "success" | "failed" | "refunded";
  transaction_type: "subscription_charge" | "plan_change" | "refund";
  created_at: string;
}

export interface BillingOverview {
  subscription: SubscriptionSummary;
  credits: CreditSummary;
  plans: PricingPlan[];
  razorpay_key_id: string | null;
  recent_ledger: CreditLedgerEntry[];
  transactions: BillingTransaction[];
}

export interface ProviderBillingRow {
  tenant_id: string;
  customer_name: string;
  plan_name: string | null;
  subscription_status: SubscriptionStatus | null;
  balance_subunits: number;
  balance_credits: string;
  balance_inr: string | null;
  in_deficit: boolean;
  current_end: string | null;
}

/**
 * GET /matching/tasks/{task_id}.
 *
 * `stages` is the inline reasoning the job page renders while a run is under
 * way. It is a fixed vocabulary the backend pipeline emits as it reaches each
 * stage, never a model narrating itself: the prompts behind this run carry a
 * real candidate's resume and a real client's JD, and a generated narration
 * could describe work that never happened.
 */
export interface MatchingTaskStatus {
  task_id: string;
  state: string;
  done: boolean;
  stages: Array<{
    key: string;
    label: string;
    detail: string;
    status: "pending" | "active" | "done" | "skipped" | "failed";
  }>;
  /** Counts of candidate ROWS being processed. Never a score or a rank. */
  candidate_count: number;
  scored_count: number;
}

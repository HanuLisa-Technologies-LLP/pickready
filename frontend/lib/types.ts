// Types mirroring docs/API_CONTRACT.md responses.

export type Role =
  | "super_admin"
  | "client"
  | "hr_manager"
  | "recruiter"
  | "hiring_manager"
  | "candidate";

export interface User {
  id: string;
  role: Role;
  tenant_id: string | null;
  full_name: string;
  email: string;
  email_verified: boolean;
  phone_verified: boolean;
}

export interface OtpRequestResponse {
  challenge_id: string;
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

/** Multi-user auth: NO cookies yet — pick a context, then select-context. */
export interface AuthContextsResponse {
  contexts: AuthContextOption[];
  context_token: string;
}

/** POST /auth/otp/verify — single user OR multiple matching users (rev 2). */
export type OtpVerifyResponse = AuthSession | AuthContextsResponse;

/** POST /auth/register-candidate — candidate self sign-up (register → login). */
export interface CandidateRegisterResponse {
  candidate_id: string;
  email: string;
  next: "login";
}

export function isContextsResponse(
  res: OtpVerifyResponse
): res is AuthContextsResponse {
  // The single-user response carries `contexts: null` (not an absent key), so
  // test the value, not key presence — otherwise every single-user login is
  // wrongly routed into the empty "choose workspace" step and never navigates.
  return Array.isArray((res as AuthContextsResponse).contexts)
    && (res as AuthContextsResponse).contexts.length > 0;
}

// ---- Admin ----

export interface Tenant {
  id: string;
  name: string;
  domain: string;
  client_email?: string;
  client_phone?: string;
  created_at?: string;
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

export interface CompanyPage {
  brief: string;
  culture: string;
  policies: string;
  benefits: string;
}

/** Roles creatable through the staff page (contract rev 2). */
export type StaffRole = "hr_manager" | "recruiter" | "hiring_manager";

/** Row from GET /companies/me/staff (contract rev 2). */
export interface StaffMember {
  id: string;
  email: string;
  full_name: string;
  phone?: string | null;
  role: StaffRole;
  status: string;
  approval_level?: string | null;
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
  reporting_to: string;
  reportees: string;
  role: string;
  responsibilities: string;
  accountabilities: string;
  education: string;
  skills: string[];
  experience_years: number;
}

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
  level: string;
  requirement_period: string;
  status: JobStatus;
  jd: JobJD;
  compensation?: Record<string, unknown> | null;
  created_at?: string;
}

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

/** One ranking parameter: 1–10 score + LLM comment (rev 2). */
export interface ParameterScore {
  score: number;
  comment: string;
}

/**
 * 4-parameter ranking breakdown (contract rev 2), stored in
 * job_candidate_links.match_breakdown_json. `overall` is the weighted
 * average (1 decimal) with a holistic 5th comment.
 */
export interface MatchBreakdown {
  skills_match: ParameterScore;
  experience_relevance: ParameterScore;
  role_alignment: ParameterScore;
  education_fit: ParameterScore;
  overall: ParameterScore;
}

export interface CandidateLink {
  link_id: string;
  candidate: CandidateSummary;
  source: CandidateSource;
  match_score?: number | null;
  tier?: Tier | null;
  status?: PipelineStatus | null;
  current_status?: PipelineStatus | null;  // backend LinkOut field name
  status_remarks?: string | null;
  hm_access_granted?: boolean;
  rationale?: string | null;
  profile_id?: string | null;
  breakdown?: MatchBreakdown | null;
}

export interface MatchingResult {
  link_id: string;
  candidate: CandidateSummary;
  source: CandidateSource;
  match_score: number;
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

// Business Development Portal types.
//
// Mirrors backend/app/schemas/bd.py verbatim (API_CONTRACT revision 9). Kept in
// its own file rather than in lib/types.ts so the BD surface can evolve without
// touching the shared type module three other portals depend on.
//
// NO NUMBERS reach the client for rated output: `confidence_label` is one of
// the approved matching words, never a score, percentage, rank or meter.

/** `bd_leads.channel`. Personal Reach and Social Reach are one funnel. */
export type BDChannel = "personal" | "social";

/**
 * `bd_leads.social_source`. REQUIRED on a social lead and forbidden on a
 * personal one, enforced by a Postgres CHECK constraint, so the create form
 * enforces it too rather than letting the database answer for it.
 */
export type BDSocialSource =
  | "linkedin"
  | "google"
  | "facebook"
  | "instagram"
  | "x";

export const SOCIAL_SOURCES: readonly BDSocialSource[] = [
  "linkedin",
  "google",
  "facebook",
  "instagram",
  "x",
] as const;

/** Display casing for a source. The API stores the lowercase slug. */
export const SOCIAL_SOURCE_LABELS: Record<BDSocialSource, string> = {
  linkedin: "LinkedIn",
  google: "Google",
  facebook: "Facebook",
  instagram: "Instagram",
  x: "X",
};

/**
 * The six progress flags, in the order models/bd.PROGRESS_FLAGS declares them.
 * Order is data on the server so the API, the CSV and this table cannot drift.
 */
export const PROGRESS_FLAGS = [
  "interaction_1",
  "interaction_2",
  "interaction_3",
  "meeting_demo_1",
  "meeting_demo_2",
  "meeting_demo_3",
] as const;

export type BDProgressFlag = (typeof PROGRESS_FLAGS)[number];

/** Short column headings. The server sends the long label on every step. */
export const PROGRESS_SHORT_LABELS: Record<BDProgressFlag, string> = {
  interaction_1: "Int 1",
  interaction_2: "Int 2",
  interaction_3: "Int 3",
  meeting_demo_1: "Meet 1",
  meeting_demo_2: "Meet 2",
  meeting_demo_3: "Meet 3",
};

/** One of the six checkboxes, exactly as the API returns it. */
export interface BDProgressStep {
  key: string;
  label: string;
  done: boolean;
  /** When the box was FIRST ticked. Survives an untick, so history is kept. */
  at: string | null;
}

export interface BDLead {
  id: string;
  channel: BDChannel;
  company_name: string;
  website: string | null;
  industry: string | null;
  location: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  social_source: BDSocialSource | null;
  /** Always all six, ticked or not. */
  progress: BDProgressStep[];
  /** null not decided, true signed, false declined. */
  agreement: boolean | null;
  agreement_at: string | null;
  /** Set once the lead was promoted to a customer. */
  tenant_id: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  notes: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface BDLeadListResponse {
  leads: BDLead[];
  /** Every lead matching the filter, not just this page. */
  total: number;
  page: number;
  page_size: number;
}

/** POST /bd/leads and PATCH /bd/leads/{id} share these fields. */
export interface BDLeadFormValues {
  company_name: string;
  website: string;
  industry: string;
  location: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  notes: string;
  /** Where the lead came from, as ANSWERED on the merged BD Reach form:
   *  a platform slug, `"direct"` for a company approached directly, or `""`
   *  for a question nobody has answered yet. It is not the stored column:
   *  `bd_leads.social_source` is NULL on a personal lead and the channel is
   *  derived from this answer (see `channelForSource`). Keeping the two apart
   *  is what lets the form tell "direct" from "unanswered", which a nullable
   *  column cannot. */
  social_source: BDSocialSource | "direct" | "";
}

// ---- Customers ----

export interface BDCustomer {
  lead_id: string;
  tenant_id: string | null;
  company_name: string;
  location: string | null;
  industry: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  website: string | null;
  channel: BDChannel;
  social_source: BDSocialSource | null;
  agreement_at: string | null;
}

export interface BDCustomerListResponse {
  customers: BDCustomer[];
  total: number;
  page: number;
  page_size: number;
}

// ---- Settings ----

export interface BDProfile {
  user_id: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  role: string;
  capabilities: string[];
}

export interface BDProfileUpdate {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
}

// ---- AI Reach ----

export interface AIReachQuery {
  job_role: string;
  city: string;
  industry: string;
  company?: string | null;
}

export type ConfidenceLabel =
  | "Highly Matching"
  | "Matching"
  | "Moderately Matching"
  | "Not Matching";

export interface BDJobCard {
  job_title: string;
  company: string;
  city: string | null;
  industry: string | null;
  /** Always present: the card's whole job is to open the company website. */
  company_url: string;
  /** Optional: a guessed posting link is worse than no link. */
  job_url: string | null;
  source_domain: string | null;
  contact_name: string | null;
  contact_role: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  contact_source_url: string | null;
  confidence_label: ConfidenceLabel;
}

/**
 * A segment's outcome. The two segments fail INDEPENDENTLY: the internet one
 * timing out must never blank the one computed from our own customers.
 */
export type BDSegmentStatus =
  | "ok"
  | "unconfigured"
  | "timeout"
  | "breaker_open"
  | "quota_exhausted"
  | "unavailable";

export interface BDSegment {
  status: BDSegmentStatus;
  /** Written for the user. Render it rather than a generic empty state. */
  message: string | null;
  jobs: BDJobCard[];
}

export interface AIReachResponse {
  query: AIReachQuery;
  similar_to_customers: BDSegment;
  from_internet: BDSegment;
}

// ---- Capabilities ----

export const BD_CAPABILITIES = {
  manageLeads: "manage_bd_leads",
  viewCustomers: "view_bd_customers",
  useAIReach: "use_ai_reach",
} as const;

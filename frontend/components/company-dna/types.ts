// The Company DNA intake, as the API describes it.
//
// Mirrors `backend/app/schemas/company_dna.py`. Kept beside the components
// that read it rather than in `lib/types.ts`, because the whole feature is one
// screen and one payload, and a type that travels further than its screen
// invites a second consumer nobody planned for.
//
// NOTHING HERE CARRIES A NUMBER A CLIENT SEES. The compiled configuration is
// numeric and it is not in these shapes: the artifact reaches this screen as
// `understanding`, which is sentences.

export type QuestionKind =
  | "scale"
  | "evidence"
  | "evidence_list"
  | "choice"
  | "text";

export interface Question {
  key: string;
  kind: QuestionKind;
  prompt: string;
  help_text: string;
  required: boolean;
  /** Scale only: the two ends, written as real alternatives. */
  poles: [string, string] | null;
  scale_min: number | null;
  scale_max: number | null;
  /** Choice only. */
  options: string[];
}

/** One of the Runbook's accepted and rejected pairs, verbatim. */
export interface EvidenceExample {
  rejected: string;
  accepted: string;
}

export interface Section {
  key: string;
  title: string;
  /** Why we are asking, in the terms a CHRO cares about. */
  intent: string;
  questions: Question[];
  answered: number;
  total: number;
  required_answered: number;
  required_total: number;
  complete: boolean;
  examples: EvidenceExample[];
  min_items: number | null;
  max_items: number | null;
  item_format: string;
}

/** One paragraph of the compiled artifact, restated without numbers. */
export interface UnderstandingBlock {
  key: string;
  title: string;
  lines: string[];
}

export interface IntakeSession {
  id: string;
  version: number;
  status: string;
  created_at: string;
  authored_by: string | null;
  sections: Section[];
  answers: Record<string, unknown>;
  next_question: Question | null;
  pending_prompt: string | null;
  answered: number;
  required: number;
  ready_to_complete: boolean;
  understanding: UnderstandingBlock[] | null;
  /**
   * The fingerprint of the understanding shown above. Sent back on completion,
   * and refused by the server if an answer moved after it was read, so a client
   * cannot confirm one reading and freeze another.
   */
  understanding_token: string | null;
}

export interface CompiledArtifact {
  version: number;
  status: string;
  completed_at: string | null;
  authored_by: string | null;
  understanding: UnderstandingBlock[];
}

export interface CompanyDnaPermissions {
  can_author: boolean;
  can_view_compiled: boolean;
  can_view_session: boolean;
}

export interface ScorecardBlock {
  blocked: boolean;
  message: string;
}

export interface CompanyDnaOverview {
  client_id: string;
  has_artifact: boolean;
  compiled: CompiledArtifact | null;
  draft_open: boolean;
  session: IntakeSession | null;
  permissions: CompanyDnaPermissions;
  scorecard: ScorecardBlock;
}

export interface CompanyDnaStatus {
  client_id: string;
  status: "complete" | "incomplete";
  version: number | null;
  completed_at: string | null;
  draft_open: boolean;
}

export interface CompanyDnaVersion {
  version: number;
  status: string;
  is_current: boolean;
  authored_by: string | null;
  created_at: string;
  completed_at: string | null;
  checksum: string | null;
}

export interface CompanyDnaVersionList {
  items: CompanyDnaVersion[];
  total: number;
  page: number;
  page_size: number;
}

/** The 422 body the API returns when Bodha refuses an answer. */
export interface AnswerRefusal {
  question_key: string;
  message: string;
}

export const companyDnaPath = (clientId: string) =>
  `/clients/${clientId}/company-dna`;

/**
 * The Layer 2 requirement, in the words spec-doc6 D3 asks for.
 *
 * The server owns this sentence: it is `SCORECARD_BLOCK_MESSAGE` in
 * `backend/app/api/company_dna.py`, and the Company DNA page renders the
 * server's copy rather than this one. This constant exists for the portal
 * banner, which reads `GET .../status` and so has no message field to render.
 *
 * `backend/tests/test_company_dna_authorization.py` reads this file and asserts
 * the two agree, because the failure being prevented is a banner that keeps
 * promising something the page has stopped saying.
 */
export const SCORECARD_BLOCK_SENTENCE =
  "Company DNA required before this job's scorecard can be locked.";

/**
 * The one place the interface asks what a person may do.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The backend has had a single authorization model for a long time: a
 * capability is resolved per request through user overlay, then tenant row,
 * then global template, then deny, and `/auth/me` returns the resolved set.
 * The interface consumed that set correctly in most places and re-derived it
 * badly in a few, and the failures all had the same shape: a screen deciding
 * for itself what "read-only" meant instead of asking.
 *
 * The company profile is the worked example. It rendered
 *
 *     {canEdit && editing ? <Save/> : <p>You have read-only access...</p>}
 *
 * so a user who HELD `edit_company_profile` and was simply not mid-edit was
 * told they did not hold it. The permission check was right and the sentence
 * under it was wrong, which is the exact contradiction the spec names: the
 * interface must never tell somebody they lack an authority the server would
 * grant them.
 *
 * So the question a component asks is now `can("edit_company_profile")`, and
 * the sentence that explains a refusal comes from `<ReadOnlyNotice>`, which
 * cannot be rendered without a capability to justify it.
 *
 * IT IS NOT A SECURITY BOUNDARY AND IS NOT TRYING TO BE
 * ----------------------------------------------------
 * Every write re-authorizes on the server. Hiding a control is a courtesy to
 * the person using the product, not a gate: a hidden button, a disabled
 * button and a guarded route are all reachable from a console, which is why
 * none of them is trusted. What this file buys is that the courtesy and the
 * gate agree.
 *
 * THIS HALF IS PURE
 * ------------------
 * Constants, the two sentences a restriction may be explained with, and the
 * rule for combining a capability answer with a resource-scoped one. It
 * imports nothing that reaches React, the auth context or Firebase, so it can
 * be unit-tested directly and imported from a Server Component. The hook that
 * reads the live session lives in `lib/use-permissions.ts` for exactly that
 * reason.
 *
 * RESOURCE-SCOPED ANSWERS COME FROM THE SERVER, NOT FROM HERE
 * -----------------------------------------------------------
 * A capability list can say "this person may edit SWOTs". It cannot say "on
 * THIS job", because assignment scope and lifecycle state are properties of
 * the job. Endpoints that carry that answer return it on the resource (the
 * SWOT analysis payload's `can_edit`, for instance) and a component prefers
 * that answer where it exists. `resolvePermission` below is how the two are
 * combined without either one being forgotten.
 */
import type { Capability } from "@/lib/types";

/**
 * The capability names the interface asks about, as constants.
 *
 * Not an exhaustive mirror of the server's list, and deliberately not: a
 * client-side copy of the whole matrix drifts, and a nav item hidden by a
 * stale copy is a page somebody is told does not exist. These are the names
 * components actually use, written once so a typo is a build error rather
 * than a silently false permission check.
 */
export const CAP = {
  editCompanyProfile: "edit_company_profile",
  editJobDescription: "edit_job_description",
  editSwot: "edit_swot",
  createJob: "create_job",
  publishJob: "publish_job",
  viewCompanyJobs: "view_company_jobs",
  viewDatabank: "view_databank",
  uploadResumes: "upload_resumes",
  triggerMatching: "trigger_matching",
  sendOutreach: "send_outreach",
  viewReviewScreen: "view_review_screen",
  decideProfile: "decide_profile",
  scheduleInterviews: "schedule_interviews",
  updatePipelineStatus: "update_pipeline_status",
  manageStaff: "manage_staff",
  assignRoles: "assign_roles",
  manageComplianceDocuments: "manage_compliance_documents",
  manageBilling: "manage_billing",
  viewBilling: "view_billing",
  manageEmailSenders: "manage_email_senders",
  authorizeEmailSenders: "authorize_email_senders",
  viewIntelligenceDashboards: "view_intelligence_dashboards",
  viewCandidateReports: "view_candidate_reports",
  viewCandidateRatings: "view_candidate_ratings",
  addTeamReviewRemark: "add_team_review_remark",
  addCompensation: "add_compensation",
  integrityDisposition: "integrity_disposition",
  /**
   * Drishti, the function's strategic profile (vivekium feature 1, C3).
   *
   * Deliberately NOT `editCompanyProfile`. Every client-side staff role
   * holds that one, including the Hiring Manager, and the brief excludes the
   * Hiring Manager by name from Drishti's audience. Asking the wider
   * capability here would show the nav entry to somebody every one of the
   * four endpoints then refuses, which is the failure this whole file exists
   * to stop: the courtesy and the gate have to agree.
   */
  authorDrishtiProfile: "author_drishti_profile",
  /**
   * The job's skills, one capability per bucket (Vivekium release, Phase 1).
   * Editing a skill is the Hiring Manager's authority, not `create_job`'s:
   * the skills are what every candidate on the job is assessed against. The
   * skills payload carries the per-job answer (`can_edit` per bucket,
   * `can_save`); these are the capability half `resolvePermission` falls back
   * to while it loads.
   */
  editMustHaveSkills: "edit_must_have_skills",
  editNiceToHaveSkills: "edit_nice_to_have_skills",
  editBehaviouralCompetencies: "edit_behavioural_competencies",
  /** Save Skills: writes the hidden assessment context and makes the job invitable. */
  finalizeRoleDefinition: "finalize_role_definition",
} as const;

export type CapabilityName = (typeof CAP)[keyof typeof CAP];

/**
 * Combine the capability answer with a resource-scoped one from the server.
 *
 * `resourceAnswer` wins whenever it is present, because it knows things the
 * capability list structurally cannot: whether this user is assigned to this
 * job, and whether the job's lifecycle state still permits the edit. While it
 * is loading (undefined), the capability answer stands, which keeps a control
 * from flickering in and out on every page load.
 */
export function resolvePermission(
  capabilityAnswer: boolean,
  resourceAnswer: boolean | null | undefined
): boolean {
  return resourceAnswer === null || resourceAnswer === undefined
    ? capabilityAnswer
    : resourceAnswer;
}

/**
 * What a read-only surface is allowed to say.
 *
 * Exported as data rather than baked into the component so a test can assert
 * that the sentence is absent from an editable surface without matching on
 * free text that somebody might reword.
 */
export const READ_ONLY_TITLE = "You have read-only access";

export function readOnlyMessage(resource: string): string {
  return `You can view ${resource} but not change it. Ask an administrator if you need editing access.`;
}

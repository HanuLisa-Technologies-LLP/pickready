/**
 * The customer team's roles, as the Staff page offers them (the leadership
 * release, 2026-09-29, spec 11 and 12).
 *
 * A MIRROR of `services/role_hierarchy` for the one thing a form needs before
 * it can ask the server anything: which roles to put in the Role dropdown.
 * The server is the authority and refuses anything this gets wrong
 * (`ensure_can_manage`, 403); what this buys is not offering a choice that
 * would be refused. Each staff row carries the server's own `can_manage`, so
 * the table's controls never read this file.
 *
 * Two shapes, as on the server:
 *   - the operational CHAIN, where a role manages every role strictly below
 *     it (Super Admin, then Recruitment Manager beside HR Manager, then
 *     Recruiter, Hiring Manager, Interview Manager);
 *   - the leadership LEAVES (CEO, MD, Functional Head), directly beneath the
 *     Super Admin, managed by the Super Admin and nobody else, managing nobody.
 */
import type { Role, StaffRole } from "@/lib/types";

export const STAFF_ROLE_LABELS: Record<StaffRole, string> = {
  recruitment_manager: "Recruitment Manager",
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
  interview_manager: "Interview Manager",
  ceo: "CEO",
  md: "MD",
  functional_head: "Functional Head",
};

/** The chain, top to bottom. The number is the rank; lower outranks higher. */
const CHAIN_RANK: Partial<Record<Role, number>> = {
  client: 0,
  recruitment_manager: 1,
  hr_manager: 1,
  recruiter: 2,
  hiring_manager: 3,
  interview_manager: 4,
};

/** The leadership leaves, in the order the dropdown lists them. */
export const LEADERSHIP_ROLES: readonly StaffRole[] = ["ceo", "md", "functional_head"];

const CHAIN_ORDER: readonly StaffRole[] = [
  "recruitment_manager",
  "hr_manager",
  "recruiter",
  "hiring_manager",
  "interview_manager",
];

/** Whether this role belongs to exactly one department (spec 13.2). The
 * server holds the same rule in `role_hierarchy.DEPARTMENT_SCOPED_ROLES` and
 * in a database CHECK. */
export function requiresDepartment(role: StaffRole): boolean {
  return role === "functional_head";
}

/** Every role `actor` may create or move somebody into, in dropdown order. */
export function manageableRoles(actor: Role | null | undefined): StaffRole[] {
  if (!actor) return [];
  if ((LEADERSHIP_ROLES as readonly string[]).includes(actor)) return [];
  const actorRank = CHAIN_RANK[actor];
  if (actorRank === undefined) return [];
  const chain = CHAIN_ORDER.filter((role) => (CHAIN_RANK[role] ?? -1) > actorRank);
  return actor === "client" ? [...chain, ...LEADERSHIP_ROLES] : chain;
}

/** A plausible mobile number: digits, an optional leading plus, spaces and
 * dashes, at most the twenty characters the server stores. */
export function isPlausiblePhone(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.length <= 20 && /^\+?[0-9][0-9\s-]{6,18}$/.test(trimmed);
}

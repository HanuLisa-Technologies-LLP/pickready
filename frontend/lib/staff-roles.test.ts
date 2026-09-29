import { describe, expect, it } from "vitest";

import {
  LEADERSHIP_ROLES,
  STAFF_ROLE_LABELS,
  isPlausiblePhone,
  manageableRoles,
  requiresDepartment,
} from "./staff-roles";

describe("manageableRoles mirrors services/role_hierarchy", () => {
  it("offers the Super Admin the whole chain and the three leadership seats", () => {
    expect(manageableRoles("client")).toEqual([
      "recruitment_manager",
      "hr_manager",
      "recruiter",
      "hiring_manager",
      "interview_manager",
      "ceo",
      "md",
      "functional_head",
    ]);
  });

  it("never offers a leadership seat below the Super Admin", () => {
    for (const actor of ["recruitment_manager", "hr_manager", "recruiter"] as const) {
      const offered = manageableRoles(actor);
      for (const leader of LEADERSHIP_ROLES) {
        expect(offered).not.toContain(leader);
      }
    }
    expect(manageableRoles("recruitment_manager")).toEqual([
      "recruiter",
      "hiring_manager",
      "interview_manager",
    ]);
  });

  it("gives a leadership role nobody to manage", () => {
    for (const leader of LEADERSHIP_ROLES) {
      expect(manageableRoles(leader)).toEqual([]);
    }
    expect(manageableRoles("interview_manager")).toEqual([]);
    expect(manageableRoles(undefined)).toEqual([]);
  });
});

describe("the staff form's rules", () => {
  it("requires a department for a Functional Head only", () => {
    expect(requiresDepartment("functional_head")).toBe(true);
    expect(requiresDepartment("ceo")).toBe(false);
    expect(requiresDepartment("recruiter")).toBe(false);
  });

  it("accepts a mobile number and refuses what is not one", () => {
    expect(isPlausiblePhone("+91 98765 43210")).toBe(true);
    expect(isPlausiblePhone("9876543210")).toBe(true);
    expect(isPlausiblePhone("call me")).toBe(false);
    expect(isPlausiblePhone("12")).toBe(false);
    expect(isPlausiblePhone("+91 98765 43210 98765 43210")).toBe(false);
  });

  it("labels every staff role", () => {
    for (const label of Object.values(STAFF_ROLE_LABELS)) {
      expect(label.length).toBeGreaterThan(0);
    }
  });
});

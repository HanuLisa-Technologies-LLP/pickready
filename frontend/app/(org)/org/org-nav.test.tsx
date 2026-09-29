// @vitest-environment jsdom
//
// Leadership Intelligence has to be REACHABLE, and only by somebody who may
// author it or read it (2026-09-29, spec 27 and 28; it replaced Drishti).
//
// The shape is worth more than the fix, and it is Drishti's own history: its
// page, its endpoints and its compilation all shipped working and nothing in
// `app/(org)/org/layout.tsx` linked to them, so nobody authored a profile and
// the layer changed nothing in any assessment. A capability-gated route with
// no way in is indistinguishable from a feature that was never built.
//
// The gate is the OTHER half. A leader AUTHORS (`author_leadership_intelligence`:
// CEO, MD, Functional Head) and a company-wide reader VIEWS
// (`view_leadership_intelligence`: the Super Admin, the CEO, the MD). Asking a
// wider capability would show the entry to somebody every endpoint refuses.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CAP } from "@/lib/permissions";

const held = new Set<string>();
const navSpy = vi.fn();

vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({ can: (name: string) => held.has(name) }),
}));

// The credit alert fetches on mount. Nothing in this file is about billing.
vi.mock("@/lib/api", () => ({
  apiGet: vi.fn().mockRejectedValue(new Error("not under test")),
}));

// The shell is where a nav item is rendered; what is under test is the LIST
// the layout hands it, so it is captured rather than drawn.
vi.mock("@/components/app-shell", () => ({
  AppShell: ({
    nav,
    children,
  }: {
    nav: { href: string; label: string }[];
    children: React.ReactNode;
  }) => {
    navSpy(nav);
    return <div>{children}</div>;
  },
}));

import OrgLayout from "./layout";

function navHrefs(): string[] {
  render(
    <OrgLayout>
      <span>content</span>
    </OrgLayout>
  );
  const last = navSpy.mock.calls.at(-1);
  if (!last) throw new Error("the layout never rendered a nav");
  return (last[0] as { href: string }[]).map((item) => item.href);
}

beforeEach(() => {
  held.clear();
  navSpy.mockClear();
});

afterEach(cleanup);

describe("the customer portal navigation", () => {
  it("offers Leadership Intelligence to a leader who authors it", () => {
    held.add(CAP.authorLeadershipIntelligence);
    expect(navHrefs()).toContain("/org/leadership");
  });

  it("offers it to a company-wide reader who cannot author it", () => {
    held.add(CAP.viewLeadershipIntelligence);
    expect(navHrefs()).toContain("/org/leadership");
  });

  it("labels it so a leader recognises it", () => {
    held.add(CAP.authorLeadershipIntelligence);
    render(
      <OrgLayout>
        <span>content</span>
      </OrgLayout>
    );
    const nav = navSpy.mock.calls.at(-1)?.[0] as { href: string; label: string }[];
    expect(nav.find((item) => item.href === "/org/leadership")?.label).toBe(
      "Leadership Intelligence"
    );
  });

  it("hides it from somebody who may do neither", () => {
    // A Hiring Manager or a Recruiter: every other staff capability, and no
    // reason to be pointed at this screen.
    held.add(CAP.editCompanyProfile);
    held.add(CAP.viewCompanyJobs);
    held.add(CAP.createJob);
    const hrefs = navHrefs();
    expect(hrefs).not.toContain("/org/leadership");
    // And the rest of the nav still rendered, so the assertion above is
    // about the gate rather than about a layout that failed to mount.
    expect(hrefs).toContain("/org/jobs");
  });

  it("gates it on the capabilities the routes themselves require", () => {
    expect(CAP.authorLeadershipIntelligence).toBe("author_leadership_intelligence");
    expect(CAP.viewLeadershipIntelligence).toBe("view_leadership_intelligence");
  });
});

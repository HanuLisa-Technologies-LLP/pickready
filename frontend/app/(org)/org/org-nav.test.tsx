// @vitest-environment jsdom
//
// Drishti has to be REACHABLE, and only by somebody who may author one.
//
// This is the whole of gap 1 in the vivekium Drishti work, and the shape is
// worth more than the fix. The page, its four endpoints, the compilation and
// the enhancement-layer wiring all shipped working, and nothing linked to
// them: `app/(org)/org/layout.tsx` enumerates every nav entry and Drishti was
// not among them. So a functional head could not find the screen, so no
// profile was ever authored, so the layer changed nothing in any assessment.
// A capability-gated route with no way in is indistinguishable from a feature
// that was never built, and no test in the product could tell the difference.
//
// The gate is the OTHER half and it is not cosmetic. `author_drishti_profile`
// is deliberately not `edit_company_profile`: every client-side staff role
// holds that one including the Hiring Manager, whom the brief excludes by
// name. Asking the wider capability here would show the entry to somebody all
// four endpoints then refuse, which is the courtesy and the gate disagreeing.

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
  it("offers Drishti to somebody who may author one", () => {
    held.add(CAP.authorDrishtiProfile);
    expect(navHrefs()).toContain("/org/drishti");
  });

  it("labels it so a functional head recognises it", () => {
    held.add(CAP.authorDrishtiProfile);
    render(
      <OrgLayout>
        <span>content</span>
      </OrgLayout>
    );
    const nav = navSpy.mock.calls.at(-1)?.[0] as { href: string; label: string }[];
    expect(nav.find((item) => item.href === "/org/drishti")?.label).toBe("Drishti");
  });

  it("hides it from somebody who may not", () => {
    // The Hiring Manager's case, which is the brief's own exclusion: they
    // hold `edit_company_profile` and every other staff capability, and they
    // still must not be pointed at this screen.
    held.add(CAP.editCompanyProfile);
    held.add(CAP.viewCompanyJobs);
    held.add(CAP.createJob);
    const hrefs = navHrefs();
    expect(hrefs).not.toContain("/org/drishti");
    // And the rest of the nav still rendered, so the assertion above is
    // about the gate rather than about a layout that failed to mount.
    expect(hrefs).toContain("/org/jobs");
  });

  it("gates it on the capability the route itself requires", () => {
    // The string, once, because the nav asking a DIFFERENT capability from
    // the endpoints is the failure mode that produces a visible link to a
    // page that 403s.
    expect(CAP.authorDrishtiProfile).toBe("author_drishti_profile");
  });
});

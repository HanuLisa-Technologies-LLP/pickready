// @vitest-environment jsdom
//
// The portal's apply dialog shows the job's skills by bucket name when the
// payload carries them (CONTRACT v10, point 2), and no skills section for a
// job whose skills were never saved.

import * as React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/components/motion", () => ({
  Stagger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  StaggerItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
// The form is not under test here: it has its own file.
vi.mock("@/components/apply-form", () => ({
  ApplyForm: () => null,
  applicationHref: (id: string | null | undefined) => `/portal/applications?focus=${id ?? ""}`,
}));

import PortalJobsPage from "./page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const LISTED = { id: "job-1", title: "Data Engineer", company_name: "Acrm Corp" };

function serve(full: Record<string, unknown>) {
  http.apiGet.mockImplementation(async (path: string) => {
    if (path === "/portal/jobs") return { jobs: [LISTED] };
    if (path === "/portal/jobs/job-1") return { ...LISTED, ...full };
    throw new Error(`unexpected read ${path}`);
  });
}

async function openApply() {
  render(<PortalJobsPage />);
  fireEvent.click(await screen.findByRole("button", { name: /Apply/ }));
  return screen.findByRole("dialog");
}

describe("the apply dialog", () => {
  it("lists the job's skills by bucket name", async () => {
    serve({
      jd_json: { description: "Build the warehouse." },
      skill_buckets: [
        { bucket: "must_have", label: "Must-have skills", names: ["Python"] },
        { bucket: "nice_to_have", label: "Nice-to-have skills", names: ["Terraform"] },
        { bucket: "behavioural", label: "Behavioural competencies", names: [] },
      ],
    });
    const dialog = await openApply();

    const mustHave = await within(dialog).findByRole("region", { name: "Must-have skills" });
    expect(within(mustHave).getByText("Python")).toBeTruthy();
    expect(within(dialog).getByText("Terraform")).toBeTruthy();
    expect(within(dialog).queryByText("Behavioural competencies")).toBeNull();
  });

  it("shows no skills section for a legacy job without saved skills", async () => {
    serve({ jd_json: { description: "Build the warehouse." }, skill_buckets: [] });
    const dialog = await openApply();

    await within(dialog).findByText("Build the warehouse.");
    expect(within(dialog).queryByText("Must-have skills")).toBeNull();
  });
});

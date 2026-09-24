// @vitest-environment jsdom
//
// Applied Jobs. Two defects this pins:
//
// * The job dialog opened with no job and rendered "This posting has closed"
//   for as long as the request took, so every candidate who clicked a title
//   was told, briefly, that their role had closed. Loading is its own state
//   now, and only a 404 reads as closed.
// * Every Updates entry links to `?application=<id>` and the page ignored it.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    detail: unknown;
    constructor(status: number, detail: unknown) {
      super(`API error ${status}`);
      this.status = status;
      this.detail = detail;
    }
  }
  return { ApiError, apiGet: vi.fn(), api: vi.fn() };
});
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));
// The shell pulls in the auth provider and, through it, a Firebase client that
// refuses to initialise without a key. The page header is all this page uses.
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import PortalApplicationsPage from "./page";

const { ApiError } = api;

function row(linkId: string, title: string) {
  return {
    link_id: linkId,
    job_id: `job-${linkId}`,
    job_title: title,
    company_name: "Acme",
    company_slug: null,
    applied_at: "2026-09-20T10:00:00+00:00",
    status: "applied",
    stage_label: "Applied",
    status_updated_at: null,
    timeline: [],
    posting_status: "active",
    posting_end_date: null,
    grace_period_end_date: null,
    can_edit: false,
    edit_closes_at: null,
    days_until_edit_closes: 0,
    assessment_invited: false,
    assessment_completed: false,
  };
}

const APPLICATIONS = {
  applications: [row("l1", "Data Engineer"), row("l2", "Platform Engineer")],
};

beforeEach(() => {
  nav.search = "";
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the job dialog", () => {
  it("says loading, never closed, while the job is on its way", async () => {
    let resolveJob: (job: unknown) => void = () => {};
    api.apiGet.mockImplementation((path: string) =>
      path === "/portal/applications"
        ? Promise.resolve(APPLICATIONS)
        : new Promise((resolve) => {
            resolveJob = resolve;
          }),
    );
    render(<PortalApplicationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Data Engineer" }));

    expect(await screen.findByText("Loading the job description")).toBeTruthy();
    expect(screen.queryByText(/posting has closed/i)).toBeNull();

    resolveJob({ id: "job-l1", title: "Data Engineer", jd: { description: "Build pipelines." } });
    await waitFor(() =>
      expect(screen.queryByText("Loading the job description")).toBeNull(),
    );
    expect(screen.queryByText(/posting has closed/i)).toBeNull();
  });

  it("reads a 404 as closed and anything else as the error it is", async () => {
    let failure: unknown = new ApiError(404, { detail: "Not found" });
    api.apiGet.mockImplementation((path: string) =>
      path === "/portal/applications"
        ? Promise.resolve(APPLICATIONS)
        : Promise.reject(failure),
    );
    render(<PortalApplicationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Data Engineer" }));
    expect(await screen.findByText(/posting has closed/i)).toBeTruthy();

    cleanup();
    failure = new ApiError(503, { detail: "Service unavailable" });
    render(<PortalApplicationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Data Engineer" }));
    expect(await screen.findByText(/could not be loaded/i)).toBeTruthy();
    expect(screen.queryByText(/posting has closed/i)).toBeNull();
  });
});

describe("?application=", () => {
  it("marks the card it names and no other", async () => {
    nav.search = "application=l2";
    api.apiGet.mockResolvedValue(APPLICATIONS);
    const { container } = render(<PortalApplicationsPage />);
    await screen.findByRole("button", { name: "Platform Engineer" });
    const marked = container.querySelectorAll('[aria-current="true"]');
    expect(marked.length).toBe(1);
    expect(marked[0].id).toBe("application-l2");
  });

  it("says so when the named application is not in the list", async () => {
    nav.search = "application=not-mine";
    api.apiGet.mockResolvedValue(APPLICATIONS);
    render(<PortalApplicationsPage />);
    expect(await screen.findByText(/not in this list/i)).toBeTruthy();
  });
});

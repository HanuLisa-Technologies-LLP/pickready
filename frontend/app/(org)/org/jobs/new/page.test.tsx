// @vitest-environment jsdom
//
// Create Job (Vivekium release, Phase 1): it saves a DRAFT and opens the job
// page, and it never publishes. A JD the server returned from its template
// carries a notice saying so until the recruiter edits it.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
const { apiGet, apiPost } = api;
const push = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: { tenant_id: "t1" } }) }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: React.ReactNode }) => <h1>{title}</h1>,
}));
vi.mock("@/components/role-type-badge", () => ({ RoleTypeBadge: () => null }));
// Radix Select has no jsdom story; a native select exercises the same
// onValueChange contract the page depends on.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: React.ReactNode;
  }) => (
    <select
      aria-label="choice"
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="">Choose</option>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: React.ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

import CreateJobPage from "./page";

const NOTICE =
  "This is a template, not an AI draft. The AI writer was unavailable. Edit it before saving.";

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  push.mockReset();
  toast.mockReset();
  apiGet.mockImplementation((path: string) => {
    if (path === "/jobs/reporting-to-options") {
      return Promise.resolve({ options: ["CTO"], other_value: "Others" });
    }
    if (path === "/companies/me/profile") {
      return Promise.resolve({ about_company: "We build payments." });
    }
    return Promise.resolve({ jobs: [], total_unresolved: 0 });
  });
});

async function fillPosition() {
  fireEvent.change(screen.getByLabelText(/^Job title/), {
    target: { value: "Backend Engineer" },
  });
  fireEvent.change(screen.getAllByLabelText("choice")[0], {
    target: { value: "managerial" },
  });
  fireEvent.change(screen.getByLabelText("Min"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Max"), { target: { value: "6" } });
}

async function generate(generatedByAi: boolean) {
  apiPost.mockResolvedValueOnce({
    jd_markdown: "## Role\nOwn the payments platform.",
    generated_by_ai: generatedByAi,
  });
  fireEvent.click(screen.getByRole("button", { name: /Generate with AI/ }));
  await waitFor(() =>
    expect(apiPost).toHaveBeenCalledWith("/jobs/generate-jd", expect.anything())
  );
}

describe("Create Job", () => {
  it("says a template is a template, until the recruiter edits it", async () => {
    render(<CreateJobPage />);
    await fillPosition();
    await generate(false);

    expect(await screen.findByText(NOTICE)).toBeTruthy();
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Template draft ready" })
    );
    expect(toast).not.toHaveBeenCalledWith(
      expect.objectContaining({ title: "Draft ready" })
    );

    // The editor opens on the fresh draft; saving the recruiter's edit makes
    // the words theirs and the notice goes.
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByText(NOTICE)).toBeNull());
  });

  it("says nothing of a template when the AI wrote the draft", async () => {
    render(<CreateJobPage />);
    await fillPosition();
    await generate(true);

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Draft ready" }))
    );
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("saves a draft, never asks to publish, and opens the job page", async () => {
    render(<CreateJobPage />);
    await fillPosition();
    await generate(true);
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    apiPost.mockResolvedValueOnce({ id: "job-9" });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/jobs", expect.anything()));
    const body = apiPost.mock.calls.find(([path]) => path === "/jobs")?.[1];
    expect(body).not.toHaveProperty("publish");
    expect(body).not.toHaveProperty("level");
    expect(body).toMatchObject({
      title: "Backend Engineer",
      grade: "managerial",
      experience_min_years: 3,
      experience_max_years: 6,
      jd_markdown: "## Role\nOwn the payments platform.",
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/org/jobs/job-9"));
    // Nothing on this page publishes: no publish route was ever called.
    expect(apiPost.mock.calls.some(([path]) => String(path).includes("/publish"))).toBe(
      false
    );
    expect(screen.queryByRole("button", { name: /Publish/ })).toBeNull();
  });
});

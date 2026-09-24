// @vitest-environment jsdom
//
// The dispute and retention panel for a closed job (vivekium release,
// Phase 6). The Close dialog promises "the assessment dispute process"; these
// tests pin that the panel keeps the promise for the person who holds the
// capability, explains the state to everybody else through the one read-only
// author, and renders the server's sentences rather than its own. The
// refusals themselves (409 live, 410 purged, the capability) are the API's
// and are tested there.

import * as React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AssessmentRetention } from "@/lib/types";

const api = vi.hoisted(() => ({
  apiDelete: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
const permissions = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({
    can: (capability: string) => permissions.granted.has(capability),
    loading: false,
    capabilities: [...permissions.granted],
  }),
}));

import { AssessmentRetentionPanel } from "./assessment-retention-panel";

const WITHHELD =
  "This job is closed, so its assessment records are no longer available here. They are retained for a short period and can be retrieved only through the assessment dispute process.";
const PURGED =
  "This job is closed and its assessment records have been permanently deleted. There is no way to retrieve them.";

function retention(overrides: Partial<AssessmentRetention>): AssessmentRetention {
  return {
    state: "pending_deletion",
    closed_at: "2026-09-20T09:00:00Z",
    purge_due_at: "2026-10-20T09:00:00Z",
    purged_at: null,
    dispute_open: false,
    days_remaining: 13,
    message: WITHHELD,
    dispute_reason: null,
    ...overrides,
  };
}

beforeEach(() => {
  api.apiGet.mockReset();
  api.apiPost.mockReset();
  api.apiDelete.mockReset();
  permissions.granted = new Set();
});
afterEach(() => cleanup());

describe("AssessmentRetentionPanel", () => {
  it("says nothing about a job that is still open", async () => {
    api.apiGet.mockResolvedValue(retention({ state: "live", message: null }));
    const { container } = render(<AssessmentRetentionPanel jobId="job-1" />);
    await waitFor(() =>
      expect(api.apiGet).toHaveBeenCalledWith("/jobs/job-1/assessment-retention")
    );
    expect(container.textContent).toBe("");
  });

  it("renders the server's sentence and the deletion date, never a count", async () => {
    api.apiGet.mockResolvedValue(retention({}));
    const { container } = render(<AssessmentRetentionPanel jobId="job-1" />);

    expect((await screen.findByTestId("retention-message")).textContent).toBe(
      WITHHELD
    );
    expect(screen.getByText(/permanently deleted on/)).toBeTruthy();
    expect(container.textContent).not.toContain("13");
    expect(container.textContent).not.toMatch(/\bdays?\b/);
  });

  it("lets the capability holder open a dispute with a stated reason", async () => {
    permissions.granted = new Set(["retrieve_disputed_assessment"]);
    api.apiGet.mockResolvedValue(retention({}));
    api.apiPost.mockResolvedValue(
      retention({ dispute_open: true, dispute_reason: "Candidate appeal" })
    );
    render(<AssessmentRetentionPanel jobId="job-1" />);

    const submit = await screen.findByRole("button", { name: "Open a dispute" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Why are these records/), {
      target: { value: "  Candidate appeal  " },
    });
    await act(async () => {
      fireEvent.click(submit);
    });

    expect(api.apiPost).toHaveBeenCalledWith("/jobs/job-1/assessment-dispute", {
      reason: "Candidate appeal",
    });
    expect(await screen.findByText("A dispute is open on this job.")).toBeTruthy();
    expect(screen.getByText("Reason recorded: Candidate appeal")).toBeTruthy();
    expect(screen.queryByTestId("read-only-notice")).toBeNull();
  });

  it("closes an open dispute with a DELETE", async () => {
    permissions.granted = new Set(["retrieve_disputed_assessment"]);
    api.apiGet.mockResolvedValue(
      retention({ dispute_open: true, dispute_reason: "Candidate appeal" })
    );
    api.apiDelete.mockResolvedValue(
      retention({ dispute_open: false, dispute_reason: null })
    );
    render(<AssessmentRetentionPanel jobId="job-1" />);

    const close = await screen.findByRole("button", { name: /Close the dispute/ });
    await act(async () => {
      fireEvent.click(close);
    });

    expect(api.apiDelete).toHaveBeenCalledWith("/jobs/job-1/assessment-dispute");
    expect(await screen.findByRole("button", { name: "Open a dispute" })).toBeTruthy();
  });

  it("offers no control without the capability and says why through the one notice", async () => {
    api.apiGet.mockResolvedValue(retention({ dispute_open: true }));
    render(<AssessmentRetentionPanel jobId="job-1" />);

    expect(await screen.findByText("A dispute is open on this job.")).toBeTruthy();
    expect(screen.getByTestId("read-only-notice")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("offers nothing once the records are gone", async () => {
    permissions.granted = new Set(["retrieve_disputed_assessment"]);
    api.apiGet.mockResolvedValue(
      retention({
        state: "purged",
        purged_at: "2026-10-20T10:00:00Z",
        message: PURGED,
        days_remaining: null,
      })
    );
    render(<AssessmentRetentionPanel jobId="job-1" />);

    expect((await screen.findByTestId("retention-message")).textContent).toBe(
      PURGED
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("read-only-notice")).toBeNull();
  });

  it("shows the server's refusal verbatim", async () => {
    permissions.granted = new Set(["retrieve_disputed_assessment"]);
    api.apiGet.mockResolvedValue(retention({}));
    api.apiPost.mockRejectedValue(new Error(PURGED));
    render(<AssessmentRetentionPanel jobId="job-1" />);

    fireEvent.change(await screen.findByLabelText(/Why are these records/), {
      target: { value: "Late appeal" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Open a dispute" }));
    });

    expect((await screen.findByRole("alert")).textContent).toContain(PURGED);
  });
});

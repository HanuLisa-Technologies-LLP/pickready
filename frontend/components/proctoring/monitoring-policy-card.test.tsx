// @vitest-environment jsdom
//
// The recruiter's one monitoring setting (proctoring spec section 6).
//
// The words are asserted verbatim because they are the specification's own,
// and because this is the sentence a recruiter reads before deciding what
// happens to a candidate who crosses the warning limit. The default is
// asserted for a stronger reason: a job nobody answered this for must be the
// permissive one, and the failure being prevented is a silent flip to
// stopping the assessment.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { apiGet, apiPatch, toast } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ apiGet, apiPatch }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import {
  DEFAULT_MONITORING_POLICY,
  MONITORING_FIELD_LABEL,
  MONITORING_HELP_TEXT,
  MONITORING_OPTIONS,
  MonitoringPolicyCard,
} from "./monitoring-policy-card";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("monitoring policy card", () => {
  it("asks the specification's question with its help text and its two options", async () => {
    apiGet.mockResolvedValue({ proctoring_warning_policy: "continue_and_note" });
    render(<MonitoringPolicyCard jobId="job-1" />);
    expect(await screen.findByText(MONITORING_FIELD_LABEL)).toBeTruthy();
    expect(screen.getByText(MONITORING_HELP_TEXT)).toBeTruthy();
    expect(screen.getByLabelText("Stop the assessment")).toBeTruthy();
    expect(screen.getByLabelText("Let them finish, just note it")).toBeTruthy();
    expect(MONITORING_OPTIONS.map((option) => option.value)).toEqual([
      "terminate",
      "continue_and_note",
    ]);
  });

  it("shows the job's current answer rather than a guess", async () => {
    apiGet.mockResolvedValue({ proctoring_warning_policy: "terminate" });
    render(<MonitoringPolicyCard jobId="job-1" />);
    await waitFor(() =>
      expect((screen.getByLabelText("Stop the assessment") as HTMLElement).getAttribute("data-state")).toBe(
        "checked"
      )
    );
  });

  it("falls back to letting them finish, never to stopping", async () => {
    // Both the job that predates the field and the job read that failed land
    // here, and in both cases stopping by default would end assessments over
    // a setting nobody chose.
    expect(DEFAULT_MONITORING_POLICY).toBe("continue_and_note");
    apiGet.mockRejectedValue(new Error("offline"));
    render(<MonitoringPolicyCard jobId="job-1" />);
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Let them finish, just note it") as HTMLElement).getAttribute("data-state")
      ).toBe("checked")
    );
  });

  it("saves the chosen option to the job", async () => {
    apiGet.mockResolvedValue({ proctoring_warning_policy: "continue_and_note" });
    apiPatch.mockResolvedValue({});
    render(<MonitoringPolicyCard jobId="job-1" />);
    fireEvent.click(await screen.findByLabelText("Stop the assessment"));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/jobs/job-1", {
        proctoring_warning_policy: "terminate",
      })
    );
  });

  it("puts the control back where it was when the save fails", async () => {
    apiGet.mockResolvedValue({ proctoring_warning_policy: "continue_and_note" });
    apiPatch.mockRejectedValue(new Error("nope"));
    render(<MonitoringPolicyCard jobId="job-1" />);
    fireEvent.click(await screen.findByLabelText("Stop the assessment"));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Let them finish, just note it") as HTMLElement).getAttribute("data-state")
      ).toBe("checked")
    );
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ variant: "destructive" }));
  });
});

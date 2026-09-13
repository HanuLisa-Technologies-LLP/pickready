// @vitest-environment jsdom
//
// The SWOT panel's permission-aware states (2026-09-13 spec, sections 27, 29,
// 30 and 32).
//
// What is worth testing in the interface is exactly the half the interface
// owns: that a user with edit access is offered controls and never the
// restriction sentence, that a view-only user is offered the sentence and
// never the controls, and that a regeneration over human-edited content asks
// first. The REFUSALS are the API's job and are tested there: a control that
// renders correctly proves nothing about a caller with a terminal.

import * as React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { READ_ONLY_TITLE } from "@/lib/permissions";
import type { SwotAnalysis } from "@/lib/types";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    apiPut: vi.fn(),
    ApiError,
  };
});
const { apiGet, apiPost, apiPut, ApiError } = api;

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({ can: () => false, loading: false, capabilities: [] }),
}));

import { JobSwotAnalysisPanel } from "./job-swot-analysis";

function analysis(overrides: Partial<SwotAnalysis> = {}): SwotAnalysis {
  return {
    job_id: "job-1",
    status: "generated",
    strengths: "A clear, senior brief with a well-understood skill profile.",
    weaknesses: "The must-have list is narrow for the salary band.",
    opportunities: "Adjacent platform engineers convert well into this role.",
    threats: "Two competitors are hiring the same profile this quarter.",
    generated_by: "ai",
    last_generated_at: "2026-09-13T10:00:00Z",
    generation_error: null,
    human_edited: false,
    last_modified_at: null,
    last_modified_by_name: null,
    version: 1,
    can_restore_previous: false,
    can_edit: true,
    ...overrides,
  };
}

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPut.mockReset();
});

describe("a user who may edit this job's SWOT", () => {
  it("is offered Edit and Regenerate, and is never told they are read-only", async () => {
    apiGet.mockResolvedValue(analysis());
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    expect(screen.getByRole("button", { name: /Edit/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Regenerate/ })).toBeTruthy();
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("edits in place and saves with the version it loaded", async () => {
    apiGet.mockResolvedValue(analysis());
    apiPut.mockResolvedValue(
      analysis({ status: "edited", human_edited: true, version: 2 })
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getByRole("button", { name: /Edit/ }));

    const field = await screen.findByLabelText("Strengths");
    fireEvent.change(field, { target: { value: "Rewritten by a human." } });
    fireEvent.click(screen.getByRole("button", { name: /Save SWOT/ }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(apiPut.mock.calls[0][0]).toBe("/jobs/job-1/swot-analysis");
    expect(apiPut.mock.calls[0][1]).toMatchObject({
      strengths: "Rewritten by a human.",
      expected_version: 1,
    });
  });

  it("offers to generate a first draft when there is no SWOT yet", async () => {
    apiGet.mockResolvedValue(
      analysis({
        status: "not_generated",
        strengths: null,
        weaknesses: null,
        opportunities: null,
        threats: null,
        generated_by: null,
        version: 0,
      })
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    expect(
      await screen.findByRole("button", { name: /Generate with AI/ })
    ).toBeTruthy();
    // Nothing to edit yet, so no Edit control and still no restriction copy.
    expect(screen.queryByRole("button", { name: /^Edit$/ })).toBeNull();
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("asks before a regeneration replaces what a person wrote", async () => {
    apiGet.mockResolvedValue(analysis({ human_edited: true, status: "edited" }));
    apiPost.mockRejectedValueOnce(
      new ApiError(409, "This SWOT has been edited by your team.")
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getByRole("button", { name: /Regenerate/ }));

    expect(
      await screen.findByText("Replace what your team wrote?")
    ).toBeTruthy();
    // The refusal did NOT replace anything: one call, unconfirmed.
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost.mock.calls[0][1]).toEqual({ confirm_overwrite: false });

    apiPost.mockResolvedValueOnce(analysis({ version: 3 }));
    fireEvent.click(
      screen.getByRole("button", { name: "Replace with a new draft" })
    );
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost.mock.calls[1][1]).toEqual({ confirm_overwrite: true });
  });

  it("renders a failed generation as a state, never as an invented SWOT", async () => {
    apiGet.mockResolvedValue(
      analysis({
        status: "failed",
        generation_error: "The SWOT writer could not be reached.",
      })
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    expect(
      await screen.findByText(/The last generation did not finish/)
    ).toBeTruthy();
    expect(
      screen.getByText("The SWOT writer could not be reached.")
    ).toBeTruthy();
    // The previously saved content is still on screen (section 30).
    expect(screen.getByText(/A clear, senior brief/)).toBeTruthy();
  });
});

describe("a user who may view but not edit", () => {
  it("reads the SWOT, is told why it is read-only, and gets no controls", async () => {
    apiGet.mockResolvedValue(analysis({ can_edit: false }));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    expect(await screen.findByText(/A clear, senior brief/)).toBeTruthy();
    expect(screen.getByText(READ_ONLY_TITLE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Regenerate/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Generate with AI/ })).toBeNull();
  });

  it("says the SWOT is empty without inviting an action they cannot take", async () => {
    apiGet.mockResolvedValue(
      analysis({
        can_edit: false,
        status: "not_generated",
        strengths: null,
        weaknesses: null,
        opportunities: null,
        threats: null,
      })
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    expect(
      await screen.findByText("No SWOT has been written for this job yet.")
    ).toBeTruthy();
  });
});

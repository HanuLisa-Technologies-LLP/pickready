// @vitest-environment jsdom
//
// The SWOT panel's permission-aware states (2026-09-13 spec, sections 27, 29,
// 30 and 32) and the three-stage presentation plus embedded reporting
// authority intake (owner ruling, 2026-09-19).
//
// What is worth testing in the interface is exactly the half the interface
// owns: that a user with edit access is offered controls and never the
// restriction sentence, that a view-only user is offered the sentence and
// never the controls, and that a regeneration over human-edited content asks
// first. The REFUSALS are the API's job and are tested there: a control that
// renders correctly proves nothing about a caller with a terminal.

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

/** The assessments router is mounted at /api/v2 ONLY, so every call this
 *  panel makes must carry the prefix in full. A relative spelling resolves to
 *  /api/v1/jobs/... and 404s, which is how this panel first shipped broken. */
const MOUNT = "/api/v2/assessments/jobs";

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

function mockReads(doc: SwotAnalysis) {
  apiGet.mockResolvedValue(doc);
}

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPut.mockReset();
});

describe("a user who may edit this job's SWOT", () => {
  it("is offered per-section Edit and a footer Regenerate, never the read-only sentence", async () => {
    mockReads(analysis());
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    expect(screen.getAllByRole("button", { name: "Edit" })).toHaveLength(4);
    expect(
      screen.getByRole("button", { name: /Regenerate with AI/ })
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Save SWOT Analysis/ })
    ).toBeTruthy();
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
    // The mount prefix, in full, at the call site (api-mount-parity).
    expect(apiGet).toHaveBeenCalledWith(`${MOUNT}/job-1/swot-analysis`);
  });

  it("edits one section in a dialog, sees it land in the draft, and saves with the version it loaded", async () => {
    mockReads(analysis());
    apiPut.mockResolvedValue(
      analysis({ status: "edited", human_edited: true, version: 2 })
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);

    const field = await screen.findByLabelText("Strengths");
    fireEvent.change(field, { target: { value: "Rewritten by a human." } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    // The dialog wrote into the LOCAL draft: visible on the page, marked
    // Edited, and nothing has reached the API yet.
    expect(await screen.findByText("Rewritten by a human.")).toBeTruthy();
    expect(screen.getByText("Edited")).toBeTruthy();
    expect(apiPut).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Save SWOT Analysis/ }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(apiPut.mock.calls[0][0]).toBe(`${MOUNT}/job-1/swot-analysis`);
    expect(apiPut.mock.calls[0][1]).toMatchObject({
      strengths: "Rewritten by a human.",
      expected_version: 1,
    });
  });

  it("offers to generate a first draft when there is no SWOT yet", async () => {
    mockReads(
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
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("sends version zero on a first hand-written save and refreshes it after success", async () => {
    mockReads(analysis({
      status: "not_generated",
      strengths: null,
      weaknesses: null,
      opportunities: null,
      threats: null,
      generated_by: null,
      version: 0,
    }));
    apiPut.mockResolvedValue(analysis({
      status: "edited", human_edited: true, version: 1,
    }));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    const field = await screen.findByLabelText("Strengths");
    fireEvent.change(field, { target: { value: "Written by the team." } });
    fireEvent.click(screen.getByRole("button", { name: /Save SWOT Analysis/ }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(apiPut.mock.calls[0][1]).toMatchObject({
      strengths: "Written by the team.", expected_version: 0,
    });

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getByRole("button", { name: /Save SWOT Analysis/ }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(2));
    expect(apiPut.mock.calls[1][1].expected_version).toBe(1);
  });

  it("sends one request for a rapid double save while the first is in flight", async () => {
    mockReads(analysis());
    let finishSave: (value: SwotAnalysis) => void = () => {};
    apiPut.mockReturnValue(new Promise<SwotAnalysis>((resolve) => {
      finishSave = resolve;
    }));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    const button = screen.getByRole("button", { name: /Save SWOT Analysis/ });
    act(() => {
      button.click();
      button.click();
    });
    expect(apiPut).toHaveBeenCalledTimes(1);
    expect(apiPut.mock.calls[0][1].expected_version).toBe(1);

    await act(async () => finishSave(analysis({ version: 2 })));
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });

  it("treats a real concurrent edit as a conflict and reloads the document", async () => {
    mockReads(analysis());
    apiPut.mockRejectedValueOnce(new ApiError(409, "Someone else saved first."));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getByRole("button", { name: /Save SWOT Analysis/ }));
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith(`${MOUNT}/job-1/swot-analysis`));
    await waitFor(() => expect(apiGet.mock.calls.filter((call) => call[0] === `${MOUNT}/job-1/swot-analysis`)).toHaveLength(2));
    expect(apiPut).toHaveBeenCalledTimes(1);
  });

  it("renders each empty section as an open field with its own placeholder and hint", async () => {
    mockReads(
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
      await screen.findByPlaceholderText(/market advantage/)
    ).toBeTruthy();
    expect(screen.getByPlaceholderText(/harder to fill/)).toBeTruthy();
    expect(screen.getByPlaceholderText(/widen the funnel/)).toBeTruthy();
    expect(screen.getByPlaceholderText(/stall this hire/)).toBeTruthy();
    expect(screen.getAllByText("Not started")).toHaveLength(4);
    expect(
      screen.getAllByText(
        "Write your own assessment, or use Generate with AI to draft this section."
      )
    ).toHaveLength(4);
    // Writing anything by hand arms the save.
    const saveButton = screen.getByRole("button", {
      name: /Save SWOT Analysis/,
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Strengths"), {
      target: { value: "A strong internal referral pipeline." },
    });
    expect(saveButton.disabled).toBe(false);
  });

  it("disables the fields and says a draft is coming while a generation runs", async () => {
    mockReads(
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
    apiPost.mockReturnValue(new Promise(() => {}));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Generate with AI/ })
    );

    const fields = await screen.findAllByPlaceholderText(
      "AI is generating a practical assessment for this section..."
    );
    expect(fields).toHaveLength(4);
    for (const field of fields) {
      expect((field as HTMLTextAreaElement).disabled).toBe(true);
    }
    expect(screen.getAllByText("AI drafting")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /Generating/ })).toBeTruthy();
  });

  it("asks before a regeneration replaces what a person wrote", async () => {
    mockReads(analysis({ human_edited: true, status: "edited" }));
    apiPost.mockRejectedValueOnce(
      new ApiError(409, "This SWOT has been edited by your team.")
    );
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    await screen.findByText(/A clear, senior brief/);
    fireEvent.click(screen.getByRole("button", { name: /Regenerate with AI/ }));

    expect(
      await screen.findByText("Replace what your team wrote?")
    ).toBeTruthy();
    // The refusal did NOT replace anything: one call, unconfirmed.
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost.mock.calls[0][0]).toBe(
      `${MOUNT}/job-1/swot-analysis/generate`
    );
    expect(apiPost.mock.calls[0][1]).toEqual({ confirm_overwrite: false });

    apiPost.mockResolvedValueOnce(analysis({ version: 3 }));
    fireEvent.click(
      screen.getByRole("button", { name: "Replace with a new draft" })
    );
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost.mock.calls[1][1]).toEqual({ confirm_overwrite: true });
  });

  it("renders a failed generation as a state, never as an invented SWOT", async () => {
    mockReads(
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

  it("uses only the Job SWOT document route", async () => {
    mockReads(analysis());
    render(<JobSwotAnalysisPanel jobId="job-1" />);
    await screen.findByText(/A clear, senior brief/);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(`${MOUNT}/job-1/swot-analysis`);
  });
});

describe("a user who may view but not edit", () => {
  it("reads the SWOT, is told why it is read-only, and gets no controls", async () => {
    mockReads(analysis({ can_edit: false }));
    render(<JobSwotAnalysisPanel jobId="job-1" />);

    expect(await screen.findByText(/A clear, senior brief/)).toBeTruthy();
    expect(screen.getByText(READ_ONLY_TITLE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Regenerate with AI/ })
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /Generate with AI/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Save SWOT Analysis/ })
    ).toBeNull();
  });

  it("says the SWOT is empty without inviting an action they cannot take", async () => {
    mockReads(
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

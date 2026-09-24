// @vitest-environment jsdom
//
// The Publish card (Vivekium release, Phase 1). The gate itself is the API's
// (PUBLISH_JOB plus a saved JD, SWOT and skills); what the card owns is that
// it reads the checklist from the server, shows the server's blocking
// sentence as written, offers Publish only to somebody holding the
// capability, and calls the ONE publish route.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { READ_ONLY_TITLE } from "@/lib/permissions";
import type { Job } from "@/lib/types";

const api = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
const { apiGet, apiPost } = api;
const perms = vi.hoisted(() => ({ held: new Set<string>(["publish_job"]) }));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({
    can: (cap: string) => perms.held.has(cap),
    loading: false,
    capabilities: [...perms.held],
  }),
}));

import { JobPublishCard, type JobSetupStatus } from "./job-publish-card";

function setup(overrides: Partial<JobSetupStatus> = {}): JobSetupStatus {
  return {
    job_id: "job-1",
    jd_ready: true,
    swot_status: "edited",
    swot_saved: true,
    skills_draft_status: "drafted",
    skills_saved: true,
    skills_locked: false,
    grade_locked: false,
    published: false,
    ready_for_candidates: true,
    publish_blocked_reason: null,
    ...overrides,
  };
}

const JOB = {
  id: "job-1",
  title: "Backend Engineer",
  public_application_url: "https://readypick.ai/apply/job-1",
} as unknown as Job;

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  perms.held = new Set(["publish_job"]);
});

describe("JobPublishCard", () => {
  it("reads the checklist from the setup route and reports it upward", async () => {
    apiGet.mockResolvedValue(setup({ skills_saved: false }));
    const onSetup = vi.fn();
    render(<JobPublishCard jobId="job-1" job={JOB} onSetup={onSetup} />);

    await screen.findByText("Skills saved");
    expect(apiGet).toHaveBeenCalledWith("/api/v2/assessments/jobs/job-1/setup");
    expect(onSetup).toHaveBeenCalledWith(expect.objectContaining({ skills_saved: false }));
    expect(screen.getAllByText("Done")).toHaveLength(2);
    expect(screen.getAllByText("Not yet")).toHaveLength(1);
  });

  it("shows the server's blocking sentence verbatim and does not offer a publish it would refuse", async () => {
    const reason =
      "Publishing needs a saved SWOT. Publishing needs saved skills.";
    apiGet.mockResolvedValue(
      setup({ swot_saved: false, skills_saved: false, publish_blocked_reason: reason })
    );
    render(<JobPublishCard jobId="job-1" job={JOB} />);

    expect(await screen.findByText(reason)).toBeTruthy();
    const button = screen.getByRole("button", { name: /Publish job/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("publishes through the one publish route and re-reads the checklist", async () => {
    apiGet
      .mockResolvedValueOnce(setup())
      .mockResolvedValue(setup({ published: true }));
    apiPost.mockResolvedValue({ ...JOB });
    const onPublished = vi.fn();
    render(<JobPublishCard jobId="job-1" job={JOB} onPublished={onPublished} />);

    fireEvent.click(await screen.findByRole("button", { name: /Publish job/ }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/jobs/job-1/publish"));
    expect(onPublished).toHaveBeenCalled();
    expect(await screen.findByText("This job is published.")).toBeTruthy();
    expect(
      (screen.getByLabelText("Application link") as HTMLInputElement).value
    ).toBe("https://readypick.ai/apply/job-1");
  });

  it("shows a publish refusal in the server's words", async () => {
    apiGet.mockResolvedValue(setup());
    apiPost.mockRejectedValue(new Error("You are not assigned to this job."));
    render(<JobPublishCard jobId="job-1" job={JOB} />);

    fireEvent.click(await screen.findByRole("button", { name: /Publish job/ }));
    expect(await screen.findByText("You are not assigned to this job.")).toBeTruthy();
  });

  it("offers no Publish control without the capability, and says so through the one notice", async () => {
    perms.held = new Set();
    apiGet.mockResolvedValue(setup());
    render(<JobPublishCard jobId="job-1" job={JOB} />);

    await screen.findByText("Skills saved");
    expect(screen.queryByRole("button", { name: /Publish job/ })).toBeNull();
    expect(screen.getByText(READ_ONLY_TITLE)).toBeTruthy();
  });

  it("says a published job cannot invite while its skills are unsaved", async () => {
    apiGet.mockResolvedValue(
      setup({ published: true, skills_saved: false, ready_for_candidates: false })
    );
    render(<JobPublishCard jobId="job-1" job={JOB} />);

    expect(
      await screen.findByText(/cannot be invited to the assessment until the skills are saved again/)
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Publish job/ })).toBeNull();
  });

  it("shows no application link for a closed job", async () => {
    apiGet.mockResolvedValue(setup({ published: true }));
    render(
      <JobPublishCard
        jobId="job-1"
        job={{ ...JOB, closed_at: "2026-09-20T00:00:00Z" } as Job}
      />
    );

    expect(
      await screen.findByText("This job has been closed. New applications have stopped.")
    ).toBeTruthy();
    expect(screen.queryByLabelText("Application link")).toBeNull();
  });
});

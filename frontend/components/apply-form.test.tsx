// @vitest-environment jsdom
//
// The ONE application form, rendered by the New Jobs dialog and by the public
// /apply page. What is pinned:
//
// * "already applied" is decided from the server's context BEFORE any field
//   renders, so nobody fills in a form that could only answer 409;
// * the request carries the resume choice, the validation answers and the
//   caller's `application_source`, and nothing the retired public form asked
//   for (no name, city, age, gender or questionnaire);
// * submitting ends on the APPLICATION, never on an assessment page;
// * a 409 asks the server which refusal it was instead of guessing from words.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiUploadWithProgress: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});

import { ApiError } from "@/lib/api";
import { ApplyForm, applicationHref, type ApplyContext } from "./apply-form";

function context(overrides: Partial<ApplyContext> = {}): ApplyContext {
  return {
    job_id: "job-1",
    already_applied: false,
    resume: { has_resume: true, filename: "main.pdf" } as ApplyContext["resume"],
    profile_complete: true,
    profile_missing: [],
    validation_fields: [],
    validation_intro: "",
    validation_values: {},
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function sentForm(): FormData {
  const [path, form] = http.apiUploadWithProgress.mock.calls[0];
  expect(path).toBe("/portal/jobs/job-1/apply");
  return form as FormData;
}

describe("already applied", () => {
  it("says so before any field renders, and links to that application", async () => {
    http.apiGet.mockResolvedValue(
      context({ already_applied: true, applied_at: "2026-09-20T10:00:00+00:00" }),
    );
    render(
      <ApplyForm jobId="job-1" jobTitle="Data Engineer" source="direct" applicationId="link-9" />,
    );
    expect(await screen.findByText("You have already applied to this role")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /submit application/i })).toBeNull();
    expect(screen.queryByText(/main resume/i)).toBeNull();
    const link = screen.getByRole("link", { name: /view your application/i });
    expect(link.getAttribute("href")).toBe("/portal/applications?application=link-9");
  });
});

describe("submitting", () => {
  it("posts the resume choice, the answers and the source, then points at the application", async () => {
    http.apiGet.mockResolvedValue(context());
    http.apiUploadWithProgress.mockResolvedValue({
      link_id: "link-1",
      job_id: "job-1",
      profile_id: "profile-1",
      resume_reused: true,
    });
    const onSubmitted = vi.fn();
    render(
      <ApplyForm
        jobId="job-1"
        jobTitle="Data Engineer"
        companyName="Acme"
        source="external_link"
        onSubmitted={onSubmitted}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /submit application/i }));

    expect(await screen.findByText("Application submitted")).toBeTruthy();
    const form = sentForm();
    expect(form.get("reuse_previous")).toBe("true");
    expect(form.get("application_source")).toBe("external_link");
    expect(form.get("validation")).toBe("{}");
    // Nothing the retired public form collected travels any more.
    for (const retired of ["full_name", "residing_city", "age", "gender", "aspects"]) {
      expect(form.has(retired)).toBe(false);
    }

    const link = screen.getByRole("link", { name: /view your application/i });
    expect(link.getAttribute("href")).toBe("/portal/applications?application=link-1");
    expect(link.getAttribute("href")).not.toContain("assessment");
    expect(onSubmitted).toHaveBeenCalledWith(expect.objectContaining({ link_id: "link-1" }));
  });

  it("refuses an upload with no file before anything is sent", async () => {
    http.apiGet.mockResolvedValue(context({ resume: { has_resume: false } as ApplyContext["resume"] }));
    render(<ApplyForm jobId="job-1" jobTitle="Data Engineer" source="direct" />);
    fireEvent.click(await screen.findByRole("button", { name: /submit application/i }));
    expect((await screen.findAllByText(/attach a pdf or docx resume/i)).length).toBeGreaterThan(0);
    expect(http.apiUploadWithProgress).not.toHaveBeenCalled();
  });

  it("asks the server what a 409 meant, and shows the already-applied state it answers", async () => {
    http.apiGet
      .mockResolvedValueOnce(context())
      .mockResolvedValueOnce(context({ already_applied: true }));
    http.apiUploadWithProgress.mockRejectedValue(
      new ApiError(409, { detail: "You have already applied to this job" }),
    );
    render(<ApplyForm jobId="job-1" jobTitle="Data Engineer" source="direct" />);
    fireEvent.click(await screen.findByRole("button", { name: /submit application/i }));
    expect(await screen.findByText("You have already applied to this role")).toBeTruthy();
    expect(http.apiGet).toHaveBeenLastCalledWith("/portal/jobs/job-1/apply-context");
  });
});

describe("a context that failed to load", () => {
  it("says so and offers a retry, rather than guessing a form", async () => {
    http.apiGet
      .mockRejectedValueOnce(new ApiError(500, { detail: "Internal error" }))
      .mockResolvedValueOnce(context());
    render(<ApplyForm jobId="job-1" jobTitle="Data Engineer" source="direct" />);
    expect(await screen.findByText(/could not load your application details/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /submit application/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /submit application/i })).toBeTruthy(),
    );
  });
});

describe("applicationHref", () => {
  it("names the application when it is known, and the list when it is not", () => {
    expect(applicationHref("a b")).toBe("/portal/applications?application=a%20b");
    expect(applicationHref(null)).toBe("/portal/applications");
  });
});

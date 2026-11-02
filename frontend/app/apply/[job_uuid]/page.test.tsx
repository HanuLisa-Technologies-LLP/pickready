// @vitest-environment jsdom
//
// The public /apply/{job} page's job read. What is pinned:
//
// * no staff route is ever tried: the public read, then the candidate's own
//   route, and nothing else (the organisation's /jobs/{id} used to be a third
//   fallback, a different audience's API behind a candidate page);
// * a role neither route serves reads as not available;
// * a read that FAILED reads as a failure with a retry, never as a closed role.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ job_uuid: "job-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apply/job-1",
}));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: null, loading: false, refresh: vi.fn(), setSession: vi.fn() }),
}));
// The sign-in widget is not under test here, and the Firebase SDK refuses to
// initialise without a real API key.
vi.mock("@/components/apply-auth", () => ({ ApplyAuth: () => null }));
vi.mock("@/lib/firebase", () => ({ firebaseAuth: {} }));
const http = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});

import { ApiError } from "@/lib/api";
import PublicApplyPage from "./page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const paths = () => http.apiGet.mock.calls.map(([path]) => String(path));

describe("reading the job", () => {
  it("tries the public route, then the candidate route, and never a staff route", async () => {
    http.apiGet.mockRejectedValue(new ApiError(404, { detail: "Not found" }));
    render(<PublicApplyPage />);
    expect(await screen.findByText("This job is not available")).toBeTruthy();
    expect(paths()).toEqual(["/jobs/public/job-1", "/portal/jobs/job-1"]);
    expect(paths().some((path) => path.startsWith("/jobs/job-1"))).toBe(false);
  });

  it("reports a failed read as a failure with a retry, not as a closed role", async () => {
    http.apiGet
      .mockRejectedValueOnce(new ApiError(503, { detail: "Service unavailable" }))
      .mockRejectedValueOnce(new ApiError(401, { detail: "Not authenticated" }));
    render(<PublicApplyPage />);
    expect(await screen.findByText("We could not load this role")).toBeTruthy();
    expect(screen.queryByText("This job is not available")).toBeNull();

    http.apiGet.mockResolvedValueOnce({ id: "job-1", title: "Data Engineer", jd_json: {} });
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: "Data Engineer" })).toBeTruthy(),
    );
  });
});

// Owner ruling 2026-11-02: the three skill tiers are the recruiting team's
// criteria and never appear on the public posting, whatever a payload carries.
describe("the posting's internal skill tiers", () => {
  const JOB = {
    id: "job-1",
    title: "Data Engineer",
    jd_json: { description: "Build the warehouse." },
    about_company: "We build payroll software.",
  };

  it("renders no tier section even when a payload carries the tiers", async () => {
    http.apiGet.mockResolvedValueOnce({
      ...JOB,
      jd_json: { description: "Build the warehouse.", skills: ["Data modelling"] },
      skill_buckets: [
        { bucket: "must_have", label: "Must-have skills", names: ["Python", "SQL"] },
        { bucket: "nice_to_have", label: "Nice-to-have skills", names: ["Terraform"] },
        {
          bucket: "behavioural",
          label: "Behavioural competencies",
          names: ["Owns incidents to closure"],
        },
      ],
    });
    render(<PublicApplyPage />);

    await screen.findByText("Build the warehouse.");
    for (const heading of [
      "Must-have skills",
      "Nice-to-have skills",
      "Behavioural competencies",
      "Behavioural skills",
    ]) {
      expect(screen.queryByText(heading)).toBeNull();
    }
    expect(screen.queryByText("Python")).toBeNull();
    expect(screen.queryByText("Owns incidents to closure")).toBeNull();
    // The general Skills section and the company narrative are unchanged.
    expect(screen.getByText("Skills")).toBeTruthy();
    expect(screen.getByText("Data modelling")).toBeTruthy();
    expect(screen.getByText("About the company")).toBeTruthy();
    expect(screen.getByText("We build payroll software.")).toBeTruthy();
  });

  it("renders the canonical JD document, the way the recruiter's preview does", async () => {
    http.apiGet.mockResolvedValueOnce({
      ...JOB,
      jd_markdown: "Own the event pipeline end to end.",
    });
    render(<PublicApplyPage />);

    expect(await screen.findByText("Own the event pipeline end to end.")).toBeTruthy();
    // The per-section blocks are the fallback for a pre-document job only.
    expect(screen.queryByText("Build the warehouse.")).toBeNull();
  });

});

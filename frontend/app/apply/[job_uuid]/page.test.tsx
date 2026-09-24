// @vitest-environment jsdom
//
// The public /apply/{job} page's job read. What is pinned:
//
// * no staff route is ever tried: the public read, then the candidate's own
//   route, and nothing else (the organisation's /jobs/{id} used to be a third
//   fallback, a different audience's API behind a candidate page);
// * a role neither route serves reads as not available;
// * a read that FAILED reads as a failure with a retry, never as a closed role.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

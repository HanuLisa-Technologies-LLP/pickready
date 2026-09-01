// @vitest-environment jsdom
//
// The candidate Projects section (Project Evidence Intelligence, 2026-09-01).
// The load-bearing claims: projects are optional and read as optional, the
// retention promise comes from the SERVER so the UI cannot drift from what
// the pipeline does, the 100-word ceiling is visible while typing, and no
// affordance implies a stored original.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
  apiUploadWithProgress: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

import { ProjectsSection } from "./projects-section";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const LIMITS = {
  max_projects: 10,
  max_files: 20,
  max_file_bytes: 25 * 1024 * 1024,
  max_total_bytes: 100 * 1024 * 1024,
  description_max_words: 100,
  supported_repository_hosts: ["github.com"],
};

const RETENTION =
  "Adding projects is optional. We analyse what you submit and keep a " +
  "structured summary of the evidence it shows; your original files are " +
  "not stored after processing.";

describe("candidate projects section", () => {
  it("renders the server's retention notice, never a local promise", async () => {
    api.apiGet.mockResolvedValue({
      retention_notice: RETENTION,
      limits: LIMITS,
      projects: [],
    });
    render(<ProjectsSection />);
    await waitFor(() =>
      expect(screen.getByText(RETENTION)).toBeTruthy()
    );
    // Optionality is stated, and no download affordance exists for originals.
    expect(screen.queryByText(/download/i)).toBeNull();
  });

  it("shows an empty state that invites but never demands", async () => {
    api.apiGet.mockResolvedValue({
      retention_notice: RETENTION,
      limits: LIMITS,
      projects: [],
    });
    render(<ProjectsSection />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /add project/i })).toBeTruthy()
    );
    expect(screen.getByText(/no projects yet/i)).toBeTruthy();
  });

  it("renders a processed project with its status word", async () => {
    api.apiGet.mockResolvedValue({
      retention_notice: RETENTION,
      limits: LIMITS,
      projects: [
        {
          id: "p1",
          name: "Garage System",
          description: "d",
          repository_url: "https://github.com/x/garage",
          submission_kind: "repository",
          status: "processed",
          status_detail: "Project evidence is ready.",
          failure_code: null,
          can_retry: false,
          files: [],
          created_at: "2026-09-01T00:00:00Z",
          processed_at: "2026-09-01T00:05:00Z",
        },
      ],
    });
    render(<ProjectsSection />);
    await waitFor(() => expect(screen.getByText("Garage System")).toBeTruthy());
    expect(screen.getByText("Evidence ready")).toBeTruthy();
    expect(screen.getByText(/github\.com\/x\/garage/)).toBeTruthy();
  });

  it("offers a retry only for retryable states", async () => {
    api.apiGet.mockResolvedValue({
      retention_notice: RETENTION,
      limits: LIMITS,
      projects: [
        {
          id: "p2",
          name: "Broken Upload",
          description: "d",
          repository_url: null,
          submission_kind: "files",
          status: "failed_extraction",
          status_detail: "No readable content was found in this submission.",
          failure_code: "nothing_extractable",
          can_retry: true,
          files: [
            {
              filename: "notes.rar",
              size_bytes: 10,
              family: "archive",
              label: "RAR",
              supported: false,
            },
          ],
          created_at: "2026-09-01T00:00:00Z",
          processed_at: null,
        },
      ],
    });
    render(<ProjectsSection />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry analysis/i })).toBeTruthy()
    );
    // Unsupported-format feedback is surfaced, not hidden.
    expect(
      screen.getByText(/could not be analysed/i)
    ).toBeTruthy();
  });
});

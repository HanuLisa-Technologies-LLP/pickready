// @vitest-environment jsdom
//
// The recruiter's view of a stored recording, after the single-mode change
// (Phase 3, 2026-09-24): there is no "mode" to report for a new session, and
// a recording made as a video interview before that says what it is.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VideoAccess } from "@/lib/types";

const { apiGet, toastApi } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  // Stable across renders, as the real hook's `toast` is: the section lists it
  // in an effect's dependency array.
  toastApi: { toast: vi.fn() },
}));

vi.mock("@/lib/api", () => ({ apiGet, apiPost: vi.fn() }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => toastApi }));

import { AssessmentVideoSection, LEGACY_FORMAT_NOTE } from "./assessment-video-section";

function access(overrides: Partial<VideoAccess> = {}): VideoAccess {
  return {
    job_candidate_link_id: "link-1",
    recording_id: "recording-1",
    assessment_mode: "conversational",
    assessment_mode_label: "Conversational",
    video_status: "Ready",
    video_status_detail: "The assessment video is ready to preview.",
    duration_seconds: 1840,
    compressed_size_bytes: 1000,
    preview_available: true,
    download_available: false,
    download_blocked_reason: null,
    can_retry: false,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AssessmentVideoSection", () => {
  it("names no mode for a session taken in the one mode there is", async () => {
    apiGet.mockResolvedValue(access());
    render(<AssessmentVideoSection linkId="link-1" />);
    expect(await screen.findByText("Ready")).toBeTruthy();
    expect(screen.queryByText("Assessment mode")).toBeNull();
    expect(screen.queryByText("Conversational")).toBeNull();
    expect(screen.queryByText(LEGACY_FORMAT_NOTE)).toBeNull();
  });

  it("says a recording made as a video interview is from an earlier format", async () => {
    apiGet.mockResolvedValue(
      access({ assessment_mode: "video_interview", assessment_mode_label: "Video interview" })
    );
    render(<AssessmentVideoSection linkId="link-1" />);
    expect(await screen.findByText(LEGACY_FORMAT_NOTE)).toBeTruthy();
    // The recording itself is still offered, exactly as before.
    expect(screen.getByRole("button", { name: /Preview video/i })).toBeTruthy();
  });

  it("names no mode when no session was ever started", async () => {
    apiGet.mockResolvedValue(
      access({
        recording_id: null,
        assessment_mode: null,
        assessment_mode_label: "Not started",
        video_status: "No recording",
        video_status_detail: "No video was recorded for this assessment.",
        duration_seconds: null,
        preview_available: false,
      })
    );
    render(<AssessmentVideoSection linkId="link-1" />);
    expect(await screen.findByText("No video was recorded for this assessment.")).toBeTruthy();
    expect(screen.queryByText("Not started")).toBeNull();
  });
});

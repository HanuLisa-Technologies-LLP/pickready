// @vitest-environment jsdom
//
// The shell's four screens and the two gates between them.
//
// The gates are the point: consent is an explicit action and nothing opens a
// device before it, and the assessment cannot begin until every system-check
// row has passed. The system check and the session runtime are mocked,
// because both need a real camera; what is exercised here is what the shell
// does with their answers, including the two the SERVER decides, a warning
// and a termination, which are driven through the callbacks the shell hands
// the runtime.

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionCallbacks } from "@/lib/proctoring/session";

const { apiGet, createSession, runSystemCheck, releaseOutcome, sessionStart, sessionStop, captured } =
  vi.hoisted(() => ({
    apiGet: vi.fn(),
    createSession: vi.fn(),
    runSystemCheck: vi.fn(),
    releaseOutcome: vi.fn(),
    sessionStart: vi.fn(),
    sessionStop: vi.fn(),
    captured: { callbacks: null as SessionCallbacks | null },
  }));

vi.mock("@/lib/api", () => ({ apiGet, apiPatch: vi.fn(), apiPost: vi.fn() }));
vi.mock("@/lib/proctoring/api", () => ({ createSession }));
vi.mock("@/lib/proctoring/session", () => ({
  SessionRuntime: class {
    constructor(_session: unknown, _media: unknown, callbacks: SessionCallbacks) {
      captured.callbacks = callbacks;
    }
    start = sessionStart;
    stop = sessionStop;
    flush = () => Promise.resolve();
    requestFullscreen = () => Promise.resolve(true);
    fieldHooksFor = () => null;
    collectAnswerBehaviour = () => null;
  },
}));
vi.mock("@/lib/proctoring/system-check", async () => {
  const actual = await vi.importActual<typeof import("@/lib/proctoring/system-check")>(
    "@/lib/proctoring/system-check"
  );
  return { ...actual, runSystemCheck, releaseOutcome };
});

import { CONSENT_ACTION, CONSENT_POINTS } from "./consent-screen";
import { useProctoring } from "./proctoring-context";
import { ANSWERS_SAVED, ProctoringShell } from "./proctoring-shell";
import { WARNING_ACKNOWLEDGE } from "./warning-modal";
import { CHECK_LABELS, buildRows, type SystemCheckOutcome } from "@/lib/proctoring/system-check";

const CONFIG = {
  max_warnings: 3,
  object_confidence_threshold: 0.65,
  object_consecutive_frames: 3,
  face_distance_threshold: 0.6,
  identity_check_interval_seconds: 30,
  obstruction_seconds: 60,
  obstruction_variance_threshold: 12,
  face_absent_moderate_seconds: 20,
  face_absent_extended_seconds: 90,
  focus_loss_ignore_under_seconds: 2,
  display_check_interval_seconds: 60,
  audio_chunk_seconds: 15,
  audio_max_chunk_bytes: 2_097_152,
  heartbeat_interval_seconds: 10,
  integrity_failure_termination_seconds: 60,
  camera_recovery_seconds: 60,
  sampling_fps_normal: 2,
  sampling_fps_confirming: 6,
  confirming_window_seconds: 5,
  sampling_fps_degraded: 1,
  low_light_luminance_threshold: 40,
  low_light_cooldown_seconds: 300,
  mouse_sample_hz: 10,
  max_keystroke_samples: 20000,
  event_batch_max: 200,
};

function outcome(passed: boolean): SystemCheckOutcome {
  const payload = {
    camera: passed,
    microphone: true,
    browser_supported: true,
    fullscreen_supported: true,
    face_detected: true,
    inference_adequate: true,
    measured_fps: 2,
  };
  return {
    rows: buildRows(payload, passed ? {} : { camera: "Allow camera access for this site." }),
    payload,
    allPassed: passed,
    camera: {} as MediaStream,
    microphone: {} as MediaStream,
    inference: { setBaseline: vi.fn(), terminate: vi.fn() } as never,
    faceDescriptor: Array.from({ length: 128 }, () => 0.1),
    deviceContext: {
      user_agent: "test",
      platform: "test",
      screen_count: 1,
      screen_width: 1280,
      screen_height: 800,
      camera_width: 640,
      camera_height: 480,
      hardware_concurrency: 8,
      webgl: true,
    },
  };
}

beforeEach(() => {
  apiGet.mockResolvedValue({ config: CONFIG, max_warnings: 3 });
  createSession.mockResolvedValue({
    session_id: "session-1",
    conversation_id: "conversation-1",
    status: "active",
    warnings_used: 0,
    max_warnings: 3,
    warning_policy: "continue_and_note",
    consented_at: "2026-09-02T10:00:00Z",
    config: CONFIG,
    audio_analysis_available: true,
  });
});

afterEach(() => {
  cleanup();
  captured.callbacks = null;
  vi.clearAllMocks();
});

/**
 * A stand-in for the assessment player: it reads the bridge exactly as the
 * conversation does, so `consumePausedMs` is exercised through the context
 * rather than through an internal the player cannot reach.
 */
function PausedProbe() {
  const bridge = useProctoring();
  const [paused, setPaused] = React.useState(0);
  return (
    <>
      <p>The first question</p>
      <button
        type="button"
        data-testid="consume-paused"
        data-paused={paused}
        onClick={() => setPaused(bridge.consumePausedMs())}
      >
        consume
      </button>
    </>
  );
}

function renderShell() {
  return render(
    <ProctoringShell linkId="link-1">
      <PausedProbe />
    </ProctoringShell>
  );
}

async function startSession() {
  runSystemCheck.mockResolvedValue(outcome(true));
  renderShell();
  fireEvent.click(screen.getByRole("button", { name: CONSENT_ACTION }));
  fireEvent.click(await screen.findByRole("button", { name: /Start the assessment/i }));
  await screen.findByText("The first question");
}

describe("consent", () => {
  it("states every one of the seven disclosures before anything is opened", () => {
    renderShell();
    for (const point of CONSENT_POINTS) {
      expect(screen.getByText(point), point).toBeTruthy();
    }
    expect(runSystemCheck).not.toHaveBeenCalled();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("does not reach the assessment or the devices until the explicit action", async () => {
    runSystemCheck.mockResolvedValue(outcome(true));
    renderShell();
    expect(screen.queryByText("The first question")).toBeNull();
    expect(runSystemCheck).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: CONSENT_ACTION }));
    await waitFor(() => expect(runSystemCheck).toHaveBeenCalled());
    expect(screen.queryByText("The first question")).toBeNull();
  });
});

describe("system check", () => {
  it("refuses to start while a row has failed, and says how to fix it", async () => {
    runSystemCheck.mockResolvedValue(outcome(false));
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: CONSENT_ACTION }));
    expect(await screen.findByText(CHECK_LABELS.camera)).toBeTruthy();
    expect(screen.getByText("Allow camera access for this site.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Start the assessment/i })).toBeNull();
    expect(screen.getByRole("button", { name: /Check again/i })).toBeTruthy();
    expect(createSession).not.toHaveBeenCalled();
  });

  it("re-runs every check on retry and offers the start once they pass", async () => {
    runSystemCheck.mockResolvedValueOnce(outcome(false)).mockResolvedValueOnce(outcome(true));
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: CONSENT_ACTION }));
    fireEvent.click(await screen.findByRole("button", { name: /Check again/i }));
    expect(await screen.findByRole("button", { name: /Start the assessment/i })).toBeTruthy();
    expect(runSystemCheck).toHaveBeenCalledTimes(2);
  });

  it("mounts the assessment only once a session exists", async () => {
    await startSession();
    expect(createSession).toHaveBeenCalledWith("link-1", expect.objectContaining({ consent: true }));
    expect(sessionStart).toHaveBeenCalled();
  });

  it("sends the descriptor and nothing resembling an image", async () => {
    await startSession();
    const body = createSession.mock.calls[0][1] as Record<string, unknown>;
    expect(body.face_descriptor).toHaveLength(128);
    expect(Object.keys(body).sort()).toEqual(
      ["consent", "device_context", "face_descriptor", "system_check"].sort()
    );
  });

  it("stays on the check screen and explains when the session cannot be created", async () => {
    createSession.mockRejectedValue(new Error("The invitation could not be found."));
    await runSystemCheck.mockResolvedValue(outcome(true));
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: CONSENT_ACTION }));
    fireEvent.click(await screen.findByRole("button", { name: /Start the assessment/i }));
    expect(await screen.findByText("The invitation could not be found.")).toBeTruthy();
    expect(screen.queryByText("The first question")).toBeNull();
  });
});

describe("the running session", () => {
  it("shows the indicator with the count the server sent", async () => {
    await startSession();
    expect(screen.getByText("Monitoring active")).toBeTruthy();
    expect(screen.getByText("No warnings so far")).toBeTruthy();
  });

  it("blocks on the server's warning message and updates the indicator to its count", async () => {
    await startSession();
    act(() => {
      captured.callbacks?.onWarning(
        {
          number: 1,
          max_warnings: 3,
          event_type: "DEVICE_DETECTED_PHONE",
          message: "A phone was detected on camera. Please move it out of view.",
          final: false,
        },
        1
      );
    });
    expect(
      await screen.findByText("A phone was detected on camera. Please move it out of view.")
    ).toBeTruthy();
    expect(screen.getByText("One warning used")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: WARNING_ACKNOWLEDGE }));
    await waitFor(() =>
      expect(screen.queryByText("A phone was detected on camera. Please move it out of view.")).toBeNull()
    );
  });

  it("accumulates the time a warning held the screen and pays it back once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await startSession();
    act(() => {
      captured.callbacks?.onWarning(
        {
          number: 1,
          max_warnings: 3,
          event_type: "FULLSCREEN_EXITED",
          message: "Please return to fullscreen.",
          final: false,
        },
        1
      );
    });
    await vi.advanceTimersByTimeAsync(4000);
    fireEvent.click(screen.getByRole("button", { name: WARNING_ACKNOWLEDGE }));
    // The player reads this when it submits; it is cleared by the read, so a
    // second answer is not credited for the same pause.
    const paused = pausedFromShell();
    expect(paused).toBeGreaterThanOrEqual(4000);
    expect(pausedFromShell()).toBe(0);
    vi.useRealTimers();
  });

  it("unmounts the assessment and explains the ending when the server terminates", async () => {
    await startSession();
    act(() => {
      captured.callbacks?.onTermination({
        reason_code: "IDENTITY_MISMATCH",
        message: "Your assessment has ended. " + ANSWERS_SAVED,
      });
    });
    expect(await screen.findByText(/Your assessment has ended\./)).toBeTruthy();
    expect(screen.queryByText("The first question")).toBeNull();
    expect(screen.queryByText("Monitoring active")).toBeNull();
    expect(sessionStop).toHaveBeenCalled();
  });
});

/**
 * The bridge's `consumePausedMs`, reached the way the player reaches it. The
 * shell provides the bridge through context, and the test's own consumer
 * below is mounted inside it by `startSession`'s children.
 */
function pausedFromShell(): number {
  const button = screen.getByTestId("consume-paused");
  act(() => {
    fireEvent.click(button);
  });
  return Number(screen.getByTestId("consume-paused").getAttribute("data-paused"));
}

// @vitest-environment jsdom
//
// The camera going away and coming back (Phase 3, 2026-09-24).
//
// Until this phase a camera loss stopped the monitor for good and the session
// ended. It now pauses, so the monitor has to SURVIVE the loss and be brought
// back, and the two kinds of loss have to be kept apart: a failed stream is
// retried on its own, a revoked permission is not (retrying it on a timer
// would put a browser prompt in front of the candidate every second) and
// waits for the browser to grant it again or for the candidate's click.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraMonitor } from "./camera";
import type { ProctoringClientConfig } from "./config";
import { DetectionRules } from "./detections";
import { DEVICE_RETRY_MS, type LossKind } from "./device-watch";
import type { InferenceClient } from "./worker-client";

class FakeTrack {
  readyState: "live" | "ended" = "live";
  onended: (() => void) | null = null;
  stop(): void {
    this.readyState = "ended";
  }
  getSettings(): MediaTrackSettings {
    return { width: 640, height: 480 };
  }
  /** The device went away: the browser ends the track and says so. */
  die(): void {
    this.readyState = "ended";
    this.onended?.();
  }
}

function fakeStream(): { stream: MediaStream; track: FakeTrack } {
  const track = new FakeTrack();
  const stream = {
    getVideoTracks: () => [track],
    getAudioTracks: () => [],
    getTracks: () => [track],
  } as unknown as MediaStream;
  return { stream, track };
}

function denied(): DOMException {
  return new DOMException("Permission denied", "NotAllowedError");
}

function notFound(): DOMException {
  return new DOMException("Requested device not found", "NotFoundError");
}

const CONFIG = {
  sampling_fps_normal: 2,
  sampling_fps_confirming: 6,
  confirming_window_seconds: 5,
  sampling_fps_degraded: 1,
  identity_check_interval_seconds: 30,
  heartbeat_interval_seconds: 10,
} as unknown as ProctoringClientConfig;

function build() {
  const getUserMedia = vi.fn<() => Promise<MediaStream>>();
  const permission = { state: "granted" as PermissionState, onchange: null as null | (() => void) };
  const nav = {
    mediaDevices: { getUserMedia },
    permissions: { query: vi.fn(() => Promise.resolve(permission)) },
  } as unknown as Navigator;
  const lost: LossKind[] = [];
  const recovered: MediaStream[] = [];
  const monitor = new CameraMonitor({
    config: CONFIG,
    inference: {} as InferenceClient,
    rules: new DetectionRules({} as ProctoringClientConfig),
    onEvent: () => undefined,
    identityChecks: false,
    onLost: (kind) => lost.push(kind),
    onRecovered: (stream) => recovered.push(stream),
    navigator: nav,
  });
  return { monitor, getUserMedia, permission, lost, recovered };
}

async function started() {
  const built = build();
  const first = fakeStream();
  await built.monitor.start(first.stream);
  // Let the permission query resolve and install its listener.
  await Promise.resolve();
  await Promise.resolve();
  return { ...built, first };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("a failed stream", () => {
  it("is reported, retried on its own, and handed back when it returns", async () => {
    const { monitor, first, getUserMedia, lost, recovered } = await started();
    getUserMedia.mockRejectedValue(notFound());
    first.track.die();
    expect(lost).toEqual(["stream"]);
    expect(monitor.status().live).toBe(false);
    expect(monitor.currentStream()).toBeNull();

    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS * 3);
    expect(getUserMedia).toHaveBeenCalledTimes(3);
    expect(recovered).toEqual([]);

    const second = fakeStream();
    getUserMedia.mockResolvedValue(second.stream);
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS);
    expect(recovered).toEqual([second.stream]);
    expect(monitor.currentStream()).toBe(second.stream);
    expect(monitor.status().live).toBe(true);

    // Recovered: the retries stop.
    const calls = getUserMedia.mock.calls.length;
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS * 5);
    expect(getUserMedia).toHaveBeenCalledTimes(calls);
    monitor.stop();
  });

  it("turns into a permission loss when the re-open is refused, and stops retrying", async () => {
    const { monitor, first, getUserMedia, lost } = await started();
    getUserMedia.mockRejectedValue(denied());
    first.track.die();
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS);
    expect(lost).toEqual(["stream", "permission"]);
    const calls = getUserMedia.mock.calls.length;
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS * 10);
    expect(getUserMedia).toHaveBeenCalledTimes(calls);
    monitor.stop();
  });

  it("stops retrying once the monitor is stopped", async () => {
    const { monitor, first, getUserMedia } = await started();
    getUserMedia.mockRejectedValue(notFound());
    first.track.die();
    monitor.stop();
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS * 5);
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});

describe("a revoked permission", () => {
  it("is reported once and never retried on a timer", async () => {
    const { monitor, first, getUserMedia, permission, lost } = await started();
    permission.state = "denied";
    permission.onchange?.();
    first.track.die();
    expect(lost).toEqual(["permission"]);
    expect(first.track.readyState).toBe("ended");
    await vi.advanceTimersByTimeAsync(DEVICE_RETRY_MS * 10);
    expect(getUserMedia).not.toHaveBeenCalled();
    monitor.stop();
  });

  it("comes back on the candidate's click", async () => {
    const { monitor, getUserMedia, permission, recovered } = await started();
    permission.state = "denied";
    permission.onchange?.();
    const back = fakeStream();
    getUserMedia.mockResolvedValue(back.stream);
    await expect(monitor.reacquire()).resolves.toBe(true);
    expect(recovered).toEqual([back.stream]);
    monitor.stop();
  });

  it("comes back on its own when the browser grants it again", async () => {
    const { monitor, getUserMedia, permission, recovered } = await started();
    permission.state = "denied";
    permission.onchange?.();
    const back = fakeStream();
    getUserMedia.mockResolvedValue(back.stream);
    permission.state = "granted";
    permission.onchange?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(recovered).toEqual([back.stream]);
    monitor.stop();
  });

  it("treats a reset to 'ask' as lost too", async () => {
    const { monitor, permission, lost } = await started();
    permission.state = "prompt";
    permission.onchange?.();
    expect(lost).toEqual(["permission"]);
    monitor.stop();
  });
});

describe("reacquire", () => {
  it("shares one attempt between concurrent callers, so no second camera light is left on", async () => {
    const { monitor, first, getUserMedia } = await started();
    first.track.die();
    const back = fakeStream();
    getUserMedia.mockResolvedValue(back.stream);
    const [a, b] = await Promise.all([monitor.reacquire(), monitor.reacquire()]);
    expect([a, b]).toEqual([true, true]);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    monitor.stop();
  });

  it("answers true at once for a camera that was never lost", async () => {
    const { monitor, getUserMedia } = await started();
    await expect(monitor.reacquire()).resolves.toBe(true);
    expect(getUserMedia).not.toHaveBeenCalled();
    monitor.stop();
  });
});

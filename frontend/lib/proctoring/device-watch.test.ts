// The device pause's client half (Phase 3, 2026-09-24).
//
// What is pinned here is WHEN each report is sent, because every one of them
// is spent by the server: a report that goes too early spends one of the
// candidate's two pauses on a cable that reseated itself, one that never goes
// leaves a session half blind with nothing paused, and a recovery sent while a
// device is still down lifts a pause the candidate still needs.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DeviceWatch } from "./device-watch";
import type { EventDraft } from "./events";

const GLITCH_MS = 5000;

function build() {
  const events: EventDraft[] = [];
  const changes: string[][] = [];
  const watch = new DeviceWatch({
    glitchMs: GLITCH_MS,
    emit: (draft) => events.push(draft),
    onChange: (lost) => changes.push([...lost]),
  });
  return { watch, events, changes, types: () => events.map((event) => event.event_type) };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a revoked permission", () => {
  it("is reported at once, with no duration", () => {
    const { watch, events } = build();
    watch.lost("camera", "permission");
    expect(events).toEqual([{ event_type: "CAMERA_PERMISSION_LOST", duration_ms: null, metadata: {} }]);
    expect(watch.isLost("camera")).toBe(true);
    expect(watch.reportedThisEpisode()).toBe(true);
  });

  it("is reported for the microphone under its own name", () => {
    const { watch, types } = build();
    watch.lost("microphone", "permission");
    expect(types()).toEqual(["MIC_PERMISSION_LOST"]);
  });

  it("is not reported twice for one outage", () => {
    const { watch, types } = build();
    watch.lost("camera", "permission");
    watch.lost("camera", "permission");
    watch.lost("camera", "stream");
    expect(types()).toEqual(["CAMERA_PERMISSION_LOST"]);
  });
});

describe("a failed stream", () => {
  it("that comes back inside the allowance is a glitch: logged, never a pause", () => {
    const { watch, events, types } = build();
    watch.lost("camera", "stream");
    vi.advanceTimersByTime(GLITCH_MS - 1);
    expect(events).toEqual([]);
    expect(watch.reportedThisEpisode()).toBe(false);
    watch.recovered("camera");
    expect(types()).toEqual(["CAMERA_STREAM_INTERRUPTED"]);
    expect(events[0].duration_ms).toBe(GLITCH_MS - 1);
    // No recovery event: nothing was paused, so there is nothing to lift.
    vi.advanceTimersByTime(GLITCH_MS * 2);
    expect(types()).toEqual(["CAMERA_STREAM_INTERRUPTED"]);
  });

  it("logs a microphone glitch as its own interruption, never a pause", () => {
    const { watch, events } = build();
    watch.lost("microphone", "stream");
    vi.advanceTimersByTime(1000);
    watch.recovered("microphone");
    expect(events).toEqual([{ event_type: "MIC_STREAM_INTERRUPTED", duration_ms: 1000, metadata: {} }]);
    expect(watch.reportedThisEpisode()).toBe(false);
  });

  it("still gone at the allowance is reported with how long it has been down", () => {
    const { watch, events } = build();
    watch.lost("microphone", "stream");
    vi.advanceTimersByTime(GLITCH_MS);
    expect(events).toEqual([{ event_type: "MIC_STREAM_FAILED", duration_ms: GLITCH_MS, metadata: {} }]);
    expect(watch.reportedThisEpisode()).toBe(true);
  });

  it("becomes a permission report the moment the refusal is known", () => {
    const { watch, types } = build();
    watch.lost("camera", "stream");
    vi.advanceTimersByTime(1000);
    watch.lost("camera", "permission");
    expect(types()).toEqual(["CAMERA_PERMISSION_LOST"]);
    // The glitch timer was cancelled: no second, stream report follows.
    vi.advanceTimersByTime(GLITCH_MS);
    expect(types()).toEqual(["CAMERA_PERMISSION_LOST"]);
    expect(watch.lossKind("camera")).toBe("permission");
  });
});

describe("the recovery", () => {
  it("is sent once, when every lost device is back, naming what was reported", () => {
    const { watch, events, types } = build();
    watch.lost("camera", "stream");
    vi.advanceTimersByTime(GLITCH_MS);
    watch.lost("microphone", "permission");
    vi.advanceTimersByTime(20_000);
    watch.recovered("camera");
    // The microphone is still down: the camera coming back lifts nothing.
    expect(types()).toEqual(["CAMERA_STREAM_FAILED", "MIC_PERMISSION_LOST"]);
    vi.advanceTimersByTime(10_000);
    watch.recovered("microphone");
    expect(types()).toEqual(["CAMERA_STREAM_FAILED", "MIC_PERMISSION_LOST", "DEVICE_RECOVERED"]);
    expect(events[2]).toEqual({
      event_type: "DEVICE_RECOVERED",
      duration_ms: GLITCH_MS + 30_000,
      metadata: { devices: ["camera", "microphone"] },
    });
    expect(watch.reportedThisEpisode()).toBe(false);
    expect(watch.lostDevices()).toEqual([]);
  });

  it("starts a fresh episode for the next loss", () => {
    const { watch, events } = build();
    watch.lost("camera", "permission");
    vi.advanceTimersByTime(3000);
    watch.recovered("camera");
    watch.lost("microphone", "permission");
    vi.advanceTimersByTime(4000);
    watch.recovered("microphone");
    const recoveries = events.filter((event) => event.event_type === "DEVICE_RECOVERED");
    expect(recoveries.map((event) => event.metadata)).toEqual([
      { devices: ["camera"] },
      { devices: ["microphone"] },
    ]);
    expect(recoveries.map((event) => event.duration_ms)).toEqual([3000, 4000]);
  });

  it("ignores a recovery for a device that was never lost", () => {
    const { watch, events } = build();
    watch.recovered("camera");
    expect(events).toEqual([]);
  });
});

describe("the watch", () => {
  it("tells its listener every time the set of lost devices changes", () => {
    const { watch, changes } = build();
    watch.lost("camera", "stream");
    vi.advanceTimersByTime(GLITCH_MS);
    watch.recovered("camera");
    expect(changes).toEqual([["camera"], ["camera"], []]);
  });

  it("sends nothing once stopped, including a glitch timer already running", () => {
    const { watch, events } = build();
    watch.lost("camera", "stream");
    watch.stop();
    vi.advanceTimersByTime(GLITCH_MS * 2);
    watch.lost("microphone", "permission");
    expect(events).toEqual([]);
  });
});

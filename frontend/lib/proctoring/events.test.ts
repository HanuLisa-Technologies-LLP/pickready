// The event queue: batching, Path A immediacy, backoff and the server's word
// on every consequence.
//
// The properties being defended, in order of how expensive they are to get
// wrong: a Path A event must not sit in a queue while the session is being
// terminated; a retriable failure must not drop an event, because a dropped
// event is a monitoring gap the report cannot see; and the client must never
// decide a warning or a termination for itself.

import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import type { EventIn, IngestOut } from "./api";
import { EventQueue } from "./events";

function ingest(overrides: Partial<IngestOut> = {}): IngestOut {
  return {
    accepted: 1,
    warnings_used: 0,
    max_warnings: 3,
    status: "active",
    warning: null,
    termination: null,
    ...overrides,
  };
}

type Post = (events: EventIn[]) => Promise<IngestOut>;

function build(post: Post, batchMax = 10) {
  const warnings: Array<{ number: number; used: number }> = [];
  const terminations: string[] = [];
  const ended: string[] = [];
  const queue = new EventQueue({
    batchMax,
    maxBackoffMs: 10_000,
    flushIntervalMs: 1000,
    post,
    onWarning: (warning, used) => warnings.push({ number: warning.number, used }),
    onTermination: (termination) => terminations.push(termination.reason_code),
    onSessionEnded: (message) => ended.push(message),
  });
  return { queue, warnings, terminations, ended };
}

describe("event queue", () => {
  it("holds a Path B event for the batch window rather than posting each one", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () => ingest());
    const { queue } = build(post);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 4000 });
    queue.enqueue({ event_type: "FULLSCREEN_EXITED" });
    queue.enqueue({ event_type: "BLOCKED_ACTION_ATTEMPTED", metadata: { action: "copy" } });
    expect(post).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[0]).toHaveLength(3);
    vi.useRealTimers();
  });

  it("posts a Path A event at once, without waiting for the window", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () => ingest());
    const { queue } = build(post);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 4000 });
    queue.enqueue({ event_type: "FACE_ABSENT_EXTENDED", duration_ms: 92_000 });
    await vi.advanceTimersByTimeAsync(0);
    expect(post).toHaveBeenCalledTimes(1);
    // The waiting Path B event rides along; what matters is that nothing
    // delayed the terminating one.
    const sent = post.mock.calls[0]?.[0] ?? [];
    expect(sent.map((event) => event.event_type)).toEqual([
      "WINDOW_FOCUS_LOST",
      "FACE_ABSENT_EXTENDED",
    ]);
    vi.useRealTimers();
  });

  it("splits at the configured batch size", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () => ingest());
    const { queue } = build(post, 2);
    for (let index = 0; index < 5; index += 1) {
      queue.enqueue({ event_type: "BLOCKED_ACTION_ATTEMPTED", metadata: { action: "copy" } });
    }
    await vi.advanceTimersByTimeAsync(5000);
    expect(post.mock.calls.map((call) => call[0].length)).toEqual([2, 2, 1]);
    vi.useRealTimers();
  });

  it("shows the warning the server issued and never one of its own", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () =>
      ingest({
        warnings_used: 2,
        warning: {
          number: 2,
          max_warnings: 3,
          event_type: "DEVICE_DETECTED_PHONE",
          message: "A phone was detected on camera. Please move it out of view.",
          final: false,
        },
      })
    );
    const { queue, warnings } = build(post);
    queue.enqueue({ event_type: "DEVICE_DETECTED_PHONE", confidence: 0.9 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(warnings).toEqual([{ number: 2, used: 2 }]);
    vi.useRealTimers();
  });

  it("stops the moment the server terminates, and posts nothing after", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () =>
      ingest({ termination: { reason_code: "IDENTITY_MISMATCH", message: "Your assessment has ended." } })
    );
    const { queue, terminations } = build(post);
    queue.enqueue({ event_type: "IDENTITY_CHECK_MISMATCH" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(terminations).toEqual(["IDENTITY_MISMATCH"]);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 3000 });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(post).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("keeps a batch a transport failure lost and retries it with backoff", async () => {
    vi.useFakeTimers();
    const post = vi
      .fn<Post>()
      .mockRejectedValueOnce(new ApiError(0, null, "offline"))
      .mockRejectedValueOnce(new ApiError(503, null, "redis unavailable"))
      .mockResolvedValue(ingest());
    const { queue } = build(post);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 5000 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(post).toHaveBeenCalledTimes(1);
    // First retry after one interval, second after two: the backoff doubles.
    await vi.advanceTimersByTimeAsync(1000);
    expect(post).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(post).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(post).toHaveBeenCalledTimes(3);
    expect(post.mock.calls[2]?.[0][0].event_type).toBe("WINDOW_FOCUS_LOST");
    expect(queue.pendingCount).toBe(0);
    vi.useRealTimers();
  });

  it("drops and counts a batch the server called malformed, which no retry repairs", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () => {
      throw new ApiError(422, null, "unprocessable");
    });
    const { queue } = build(post);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 5000 });
    await vi.advanceTimersByTimeAsync(5000);
    expect(post).toHaveBeenCalledTimes(1);
    expect(queue.rejected).toBe(1);
    expect(queue.pendingCount).toBe(0);
    vi.useRealTimers();
  });

  it("treats a 409 as the session being over and says so once", async () => {
    vi.useFakeTimers();
    const post = vi.fn<Post>(async () => {
      const error = new ApiError(409, null);
      error.message = "This assessment has ended.";
      throw error;
    });
    const { queue, ended } = build(post);
    queue.enqueue({ event_type: "WINDOW_FOCUS_LOST", duration_ms: 5000 });
    await vi.advanceTimersByTimeAsync(5000);
    expect(ended).toEqual(["This assessment has ended."]);
    expect(post).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

// The Run client's pure parts: the token a click carries, and the poll that
// waits for a run without hammering the server or waiting for ever.

import { describe, expect, it, vi } from "vitest";

import { ApiError, NETWORK_ERROR } from "@/lib/api";
import {
  nextPollDelay,
  pollRun,
  POLL_GIVE_UP_MS,
  POLL_INITIAL_MS,
  POLL_MAX_MS,
  POLL_NETWORK_FAILURES,
  runsPath,
  RunStillRunning,
  tokenForRun,
  type CodingRunOut,
} from "./coding";

const ATTEMPT = { questionId: "q-1", language: "python", source: "print(1)\n" };

describe("the client token", () => {
  it("reuses the pending token only for the same code, language and question", () => {
    const mint = vi.fn().mockReturnValueOnce("t-1").mockReturnValueOnce("t-2");
    const first = tokenForRun(null, ATTEMPT, mint);
    expect(first).toEqual({ token: "t-1", ...ATTEMPT });
    expect(tokenForRun(first, ATTEMPT, mint)).toBe(first);
    expect(mint).toHaveBeenCalledTimes(1);

    for (const changed of [
      { ...ATTEMPT, source: "print(2)\n" },
      { ...ATTEMPT, language: "java" },
      // The server keys a token on the conversation, so the same starter on
      // the next question must not be answered with this question's run.
      { ...ATTEMPT, questionId: "q-2" },
    ]) {
      const mintAgain = vi.fn().mockReturnValue("t-new");
      expect(tokenForRun(first, changed, mintAgain).token).toBe("t-new");
    }
  });
});

describe("the route", () => {
  it("is the assessment mount's full v2 path, with every segment encoded", () => {
    expect(runsPath("c/1", "q 2")).toBe("/api/v2/assessments/conversations/c%2F1/coding/q%202/runs");
  });
});

function run(status: CodingRunOut["status"]): CodingRunOut {
  return { run_id: "r", status, tests: [], message: null };
}

/** A clock that moves only when the poll waits. */
function clock() {
  let now = 0;
  const waits: number[] = [];
  return {
    now: () => now,
    wait: async (ms: number) => {
      waits.push(ms);
      now += ms;
    },
    waits,
  };
}

describe("polling", () => {
  it("backs off from half a second to two and stops at the first final state", async () => {
    const time = clock();
    const fetchOnce = vi
      .fn()
      .mockResolvedValueOnce(run("queued"))
      .mockResolvedValueOnce(run("queued"))
      .mockResolvedValueOnce(run("queued"))
      .mockResolvedValueOnce(run("queued"))
      .mockResolvedValueOnce(run("complete"));
    const result = await pollRun({
      conversationId: "c",
      questionId: "q",
      runId: "r",
      fetchOnce,
      wait: time.wait,
      now: time.now,
    });
    expect(result.status).toBe("complete");
    expect(time.waits[0]).toBe(POLL_INITIAL_MS);
    expect(time.waits).toEqual([500, 750, 1125, 1688, 2000]);
    expect(Math.max(...time.waits)).toBe(POLL_MAX_MS);
    expect(nextPollDelay(POLL_MAX_MS)).toBe(POLL_MAX_MS);
  });

  it.each(["unavailable", "failed"] as const)("treats %s as final", async (status) => {
    const time = clock();
    const result = await pollRun({
      conversationId: "c",
      questionId: "q",
      runId: "r",
      fetchOnce: vi.fn().mockResolvedValue(run(status)),
      wait: time.wait,
      now: time.now,
    });
    expect(result.status).toBe(status);
  });

  it("gives up with a sentence after the ceiling instead of waiting for ever", async () => {
    const time = clock();
    const fetchOnce = vi.fn().mockResolvedValue(run("queued"));
    await expect(
      pollRun({ conversationId: "c", questionId: "q", runId: "r", fetchOnce, wait: time.wait, now: time.now })
    ).rejects.toBeInstanceOf(RunStillRunning);
    expect(time.now()).toBeGreaterThanOrEqual(POLL_GIVE_UP_MS);
    expect(new RunStillRunning().message).not.toMatch(/\d/);
  });

  it("retries a network blip, and reports a dead connection after the limit", async () => {
    const offline = new ApiError(NETWORK_ERROR, null, "We couldn't reach the server.");
    const time = clock();
    const recovering = vi
      .fn()
      .mockRejectedValueOnce(offline)
      .mockRejectedValueOnce(offline)
      .mockResolvedValueOnce(run("complete"));
    await expect(
      pollRun({ conversationId: "c", questionId: "q", runId: "r", fetchOnce: recovering, wait: time.wait, now: time.now })
    ).resolves.toMatchObject({ status: "complete" });

    const dead = vi.fn().mockRejectedValue(offline);
    await expect(
      pollRun({ conversationId: "c", questionId: "q", runId: "r", fetchOnce: dead, wait: clock().wait, now: clock().now })
    ).rejects.toBe(offline);
    expect(dead).toHaveBeenCalledTimes(POLL_NETWORK_FAILURES);
  });

  it("stops at once on a refusal from the server, which is not a blip", async () => {
    const refused = new ApiError(404, { detail: "Not found." });
    const fetchOnce = vi.fn().mockRejectedValue(refused);
    await expect(
      pollRun({ conversationId: "c", questionId: "q", runId: "r", fetchOnce, wait: clock().wait, now: clock().now })
    ).rejects.toBe(refused);
    expect(fetchOnce).toHaveBeenCalledTimes(1);
  });

  it("stops when the question is left", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      pollRun({
        conversationId: "c",
        questionId: "q",
        runId: "r",
        signal: controller.signal,
        fetchOnce: vi.fn(),
      })
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});


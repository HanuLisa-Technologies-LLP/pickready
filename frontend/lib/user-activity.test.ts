/**
 * The idle deadline moves for a person, never for a timer.
 *
 * The server renews the thirty-minute deadline only for a request carrying
 * `X-User-Activity: 1`. These tests pin both halves of the client's side: the
 * header appears only within a few seconds of a real interaction, and every
 * request path in `lib/api.ts` (JSON, raw fetch, and the refresh a 401
 * triggers) carries it then and omits it otherwise. A poll that ran with the
 * header would be the old bug by another route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, apiFetch, tryRefresh } from "./api";
import {
  ACTIVITY_HEADER,
  INTERACTION_EVENTS,
  RECENT_INTERACTION_MS,
  activityHeaders,
  installInteractionTracking,
  isRecentInteraction,
  markInteraction,
  resetInteractions,
} from "./user-activity";

beforeEach(() => resetInteractions());
afterEach(() => {
  resetInteractions();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("recent interaction", () => {
  it("is false before anything happened", () => {
    expect(isRecentInteraction()).toBe(false);
    expect(activityHeaders()).toEqual({});
  });

  it("holds for the window and lapses after it", () => {
    markInteraction(1_000_000);
    expect(isRecentInteraction(RECENT_INTERACTION_MS, 1_000_000 + RECENT_INTERACTION_MS)).toBe(true);
    expect(isRecentInteraction(RECENT_INTERACTION_MS, 1_000_001 + RECENT_INTERACTION_MS)).toBe(false);
    expect(activityHeaders(1_000_000 + 10)).toEqual({ [ACTIVITY_HEADER]: "1" });
    expect(activityHeaders(1_000_001 + RECENT_INTERACTION_MS)).toEqual({});
  });
});

describe("interaction tracking", () => {
  it("marks on every interaction event and stops when removed", () => {
    const target = new EventTarget();
    const stop = installInteractionTracking(target);
    for (const name of INTERACTION_EVENTS) {
      resetInteractions();
      target.dispatchEvent(new Event(name));
      expect(isRecentInteraction()).toBe(true);
    }
    stop();
    resetInteractions();
    target.dispatchEvent(new Event("pointerdown"));
    expect(isRecentInteraction()).toBe(false);
  });

  it("does not treat a scroll or a focus change as a person acting", () => {
    const target = new EventTarget();
    const stop = installInteractionTracking(target);
    target.dispatchEvent(new Event("scroll"));
    target.dispatchEvent(new Event("visibilitychange"));
    target.dispatchEvent(new Event("focus"));
    expect(isRecentInteraction()).toBe(false);
    stop();
  });
});

function recordingFetch(status = 200) {
  const calls: Headers[] = [];
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    calls.push(new Headers(init?.headers));
    return new Response("{}", { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

describe("every request path in lib/api.ts", () => {
  it("sends the header after an interaction and not without one", async () => {
    const calls = recordingFetch();
    await api("/jobs");
    await apiFetch("/resume/preview");
    markInteraction();
    await api("/jobs");
    await apiFetch("/resume/preview");
    expect(calls.map((headers) => headers.get(ACTIVITY_HEADER))).toEqual([
      null,
      null,
      "1",
      "1",
    ]);
  });

  it("keeps the caller's own headers on the raw path", async () => {
    const calls = recordingFetch();
    markInteraction();
    await apiFetch("/upload", { method: "POST", headers: { "X-Custom": "kept" } });
    expect(calls[0].get("X-Custom")).toBe("kept");
    expect(calls[0].get(ACTIVITY_HEADER)).toBe("1");
  });

  it("does not let a poll's refresh renew the session", async () => {
    const calls = recordingFetch();
    expect(await tryRefresh()).toBe(true);
    expect(calls[0].get(ACTIVITY_HEADER)).toBeNull();
  });
});

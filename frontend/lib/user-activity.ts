/**
 * Real user activity, and the one header that tells the server about it.
 *
 * WHY THIS EXISTS
 * ---------------
 * The server's thirty-minute idle deadline used to move on EVERY authenticated
 * request, so a tab left open on a page that polls (a matching run, the
 * messages list, a countdown) kept its session alive for ever. When the
 * fifteen-minute access token lapsed, the poll's 401 was repaired by a refresh
 * that renewed the deadline too.
 *
 * Now the server renews only when a request carries `X-User-Activity: 1`
 * (`backend/app/api/deps.py`, `ACTIVITY_HEADER`), and this module decides when
 * to send it: within a few seconds of a real pointer, key or touch event. A
 * request fired by a timer carries nothing and renews nothing. The header is
 * not a credential; forging it only keeps the forger's own session alive,
 * which a real click would also do.
 *
 * `lib/api.ts` adds the header to every request path it owns (`api`,
 * `apiFetch`, `apiUploadWithProgress`, and the refresh), and
 * `lib/auth-context.tsx` installs the listeners once.
 */

/** The request header the backend reads. Must match `deps.ACTIVITY_HEADER`. */
export const ACTIVITY_HEADER = "X-User-Activity";
export const ACTIVITY_HEADER_VALUE = "1";

/**
 * How recent an interaction must be for a request to count as activity. Long
 * enough for a click to reach its request (and a 401 to reach its refresh),
 * short enough that a poll firing a few seconds later does not ride along.
 */
export const RECENT_INTERACTION_MS = 5000;

/** The DOM events that mean a person did something. */
export const INTERACTION_EVENTS = ["pointerdown", "keydown", "touchstart"] as const;

let lastInteractionAt = 0;

/** Record that a person interacted with the page just now. */
export function markInteraction(now: number = Date.now()): void {
  lastInteractionAt = now;
}

/** Whether a person interacted within the last `windowMs` milliseconds. */
export function isRecentInteraction(
  windowMs: number = RECENT_INTERACTION_MS,
  now: number = Date.now(),
): boolean {
  return lastInteractionAt > 0 && now - lastInteractionAt <= windowMs;
}

/** The activity header when the request follows a real interaction, else none. */
export function activityHeaders(now: number = Date.now()): Record<string, string> {
  return isRecentInteraction(RECENT_INTERACTION_MS, now)
    ? { [ACTIVITY_HEADER]: ACTIVITY_HEADER_VALUE }
    : {};
}

/** Forget every recorded interaction. For tests. */
export function resetInteractions(): void {
  lastInteractionAt = 0;
}

type Listenable = Pick<EventTarget, "addEventListener" | "removeEventListener">;

/**
 * Listen for real interaction on `target` (the window by default) and return
 * the function that stops listening.
 *
 * CAPTURE phase and passive, so the mark is recorded before any handler on the
 * page runs (including one that fires a request) and cannot slow a scroll.
 */
export function installInteractionTracking(target?: Listenable): () => void {
  const node: Listenable | undefined =
    target ?? (typeof window === "undefined" ? undefined : window);
  if (!node) return () => {};
  const onInteraction = () => markInteraction();
  const options: AddEventListenerOptions = { capture: true, passive: true };
  for (const name of INTERACTION_EVENTS) {
    node.addEventListener(name, onInteraction, options);
  }
  return () => {
    for (const name of INTERACTION_EVENTS) {
      node.removeEventListener(name, onInteraction, options);
    }
  };
}

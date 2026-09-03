/**
 * Fullscreen and focus tracking (proctoring spec 4.2, first two rows).
 *
 * Two different questions, kept apart because they have different debounces:
 *
 *   FULLSCREEN_EXITED   reported the moment the document leaves fullscreen,
 *                       no debounce. Leaving fullscreen is a deliberate act.
 *   WINDOW_FOCUS_LOST   reported when the candidate comes BACK, with how long
 *                       they were away, and only when that was at least
 *                       `focus_loss_ignore_under_seconds`. A notification
 *                       stealing focus for half a second is not leaving the
 *                       assessment, and the server applies the same rule
 *                       again because the client's clock is not trusted.
 *
 * "Away" is one state computed from two signals, the tab being hidden and the
 * window losing focus, because a candidate can leave either way and the
 * browser fires a different pair of events for each.
 *
 * Fullscreen can only be REQUESTED from a user gesture, so this module never
 * requests it on its own; the shell calls `requestFullscreen` from the Start
 * button and again from the warning's acknowledge button.
 */
import type { EventDraft } from "./events";

export interface FocusHandle {
  release(): void;
  isFullscreen(): boolean;
  requestFullscreen(): Promise<boolean>;
  /** True while the lockdown expects the document to be fullscreen. */
  installed(): boolean;
}

export interface FocusOptions {
  ignoreUnderMs: number;
  onEvent: (draft: EventDraft) => void;
  document?: Document;
  window?: Window;
  now?: () => number;
}

export function isFullscreenSupported(doc: Document = document): boolean {
  return typeof doc.documentElement.requestFullscreen === "function";
}

export function watchFocus(options: FocusOptions): FocusHandle {
  const doc = options.document ?? document;
  const win = options.window ?? window;
  const now = options.now ?? Date.now;
  let awaySince: number | null = null;
  let released = false;

  const isAway = () => doc.visibilityState === "hidden" || (typeof doc.hasFocus === "function" && !doc.hasFocus());

  const reconcile = () => {
    const away = isAway();
    if (away && awaySince === null) {
      awaySince = now();
      return;
    }
    if (!away && awaySince !== null) {
      const duration = now() - awaySince;
      awaySince = null;
      if (duration >= options.ignoreUnderMs) {
        options.onEvent({
          event_type: "WINDOW_FOCUS_LOST",
          duration_ms: Math.round(duration),
          metadata: {},
        });
      }
    }
  };

  const onFullscreenChange = () => {
    if (doc.fullscreenElement === null) {
      options.onEvent({ event_type: "FULLSCREEN_EXITED", metadata: {} });
    }
  };

  doc.addEventListener("visibilitychange", reconcile);
  win.addEventListener("blur", reconcile);
  win.addEventListener("focus", reconcile);
  doc.addEventListener("fullscreenchange", onFullscreenChange);

  return {
    isFullscreen: () => doc.fullscreenElement !== null,
    installed: () => !released,
    requestFullscreen: async () => {
      if (doc.fullscreenElement !== null) return true;
      if (!isFullscreenSupported(doc)) return false;
      try {
        await doc.documentElement.requestFullscreen({ navigationUI: "hide" });
        return true;
      } catch {
        // Refused without a user gesture, or by a browser policy. The caller
        // is told, and the next gesture is the next chance.
        return false;
      }
    },
    release: () => {
      released = true;
      doc.removeEventListener("visibilitychange", reconcile);
      win.removeEventListener("blur", reconcile);
      win.removeEventListener("focus", reconcile);
      doc.removeEventListener("fullscreenchange", onFullscreenChange);
    },
  };
}

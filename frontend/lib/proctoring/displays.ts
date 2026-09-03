/**
 * Multiple-display detection (proctoring spec 4.2, last row).
 *
 * `window.screen.isExtended` is the one signal a page gets without asking for
 * a permission: whether the device has more than one screen attached. It is
 * read at start and every `display_check_interval_seconds`. The finer
 * `getScreenDetails()` (screen count, layout) needs the `window-management`
 * permission, which shows a prompt; it is read ONLY when that permission is
 * already granted, because a permission prompt appearing mid-assessment
 * would be a focus loss the candidate did not cause.
 *
 * The event is reported when the check first finds a second screen, and
 * again only when a screen is attached after being absent. Repeating it every
 * minute would fill the activity log with the same fact. The server already
 * treats a repeat as logged-only.
 *
 * True screen MIRRORING is invisible to a page (spec section 13) and nothing
 * here claims to see it.
 */
import type { EventDraft } from "./events";

interface ExtendedScreen extends Screen {
  isExtended?: boolean;
}

interface ScreenDetails {
  screens: ReadonlyArray<unknown>;
}

interface WindowWithScreens extends Window {
  getScreenDetails?: () => Promise<ScreenDetails>;
}

export interface DisplaysHandle {
  release(): void;
  /** The screen count when the permission allows it, else null. */
  check(): Promise<number | null>;
}

export interface DisplaysOptions {
  intervalMs: number;
  onEvent: (draft: EventDraft) => void;
  window?: Window;
}

export function isExtended(win: Window = window): boolean {
  return (win.screen as ExtendedScreen).isExtended === true;
}

async function screenCount(win: WindowWithScreens): Promise<number | null> {
  if (typeof win.getScreenDetails !== "function" || !win.navigator.permissions) return null;
  try {
    const status = await win.navigator.permissions.query({
      name: "window-management" as PermissionName,
    });
    if (status.state !== "granted") return null;
    const details = await win.getScreenDetails();
    return details.screens.length;
  } catch {
    // The permission name is not known to this browser. Nothing to read.
    return null;
  }
}

export function watchDisplays(options: DisplaysOptions): DisplaysHandle {
  const win = (options.window ?? window) as WindowWithScreens;
  let reportedExtended = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  const check = async (): Promise<number | null> => {
    const extended = isExtended(win);
    const count = extended ? await screenCount(win) : null;
    if (extended && !reportedExtended) {
      options.onEvent({
        event_type: "MULTIPLE_DISPLAYS_DETECTED",
        metadata: count === null ? {} : { screens: count },
      });
    }
    reportedExtended = extended;
    return count;
  };

  void check();
  timer = setInterval(() => void check(), options.intervalMs);

  return {
    check,
    release: () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    },
  };
}

// @vitest-environment jsdom
//
// The lockdown blocks every action proctoring spec 4.4 lists, and reports
// each attempt.
//
// Asserted on `defaultPrevented` rather than on a spy: the requirement is
// that the browser does not perform the action, and a handler that called
// `preventDefault` on the wrong object would satisfy a spy and not the
// requirement. What is deliberately NOT asserted anywhere is that these are
// unbreakable; the module's own header says they are not.

import { afterEach, describe, expect, it, vi } from "vitest";

import { classifyKeydown, installLockdown, type BlockedAction, type LockdownHandle } from "./lockdown";

let handle: LockdownHandle | null = null;

afterEach(() => {
  handle?.release();
  handle = null;
});

function install(): BlockedAction[] {
  const reported: BlockedAction[] = [];
  handle = installLockdown({ onBlocked: (action) => reported.push(action) });
  return reported;
}

function dispatch(type: string, init: EventInit = {}): Event {
  const event = new Event(type, { bubbles: true, cancelable: true, ...init });
  document.body.dispatchEvent(event);
  return event;
}

function key(init: KeyboardEventInit): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  document.body.dispatchEvent(event);
  return event;
}

describe("lockdown", () => {
  it("cancels copy, cut, paste, the context menu and a drop, and reports each", () => {
    const reported = install();
    for (const type of ["copy", "cut", "paste", "contextmenu", "drop"]) {
      expect(dispatch(type).defaultPrevented, type).toBe(true);
    }
    expect(reported).toEqual(["copy", "cut", "paste", "context_menu", "drop"]);
  });

  it("cancels dragover so a drop can be refused at all, without reporting it", () => {
    // dragover fires continuously while something is dragged; reporting it
    // would bury the one attempt in hundreds of moves.
    const reported = install();
    expect(dispatch("dragover").defaultPrevented).toBe(true);
    expect(reported).toEqual([]);
  });

  it("swallows every developer-tools, print, save, find and view-source shortcut", () => {
    const reported = install();
    const attempts: Array<[KeyboardEventInit, BlockedAction]> = [
      [{ key: "F12" }, "developer_tools"],
      [{ key: "I", ctrlKey: true, shiftKey: true }, "developer_tools"],
      [{ key: "J", ctrlKey: true, shiftKey: true }, "developer_tools"],
      [{ key: "C", ctrlKey: true, shiftKey: true }, "developer_tools"],
      [{ key: "I", metaKey: true, altKey: true }, "developer_tools"],
      [{ key: "p", ctrlKey: true }, "print"],
      [{ key: "s", metaKey: true }, "save"],
      [{ key: "f", ctrlKey: true }, "find"],
      [{ key: "u", ctrlKey: true }, "view_source"],
    ];
    for (const [init, expected] of attempts) {
      const event = key(init);
      expect(event.defaultPrevented, JSON.stringify(init)).toBe(true);
      expect(classifyKeydown(event)).toBe(expected);
    }
    expect(reported).toEqual(attempts.map(([, action]) => action));
  });

  it("leaves ordinary typing alone, including the submit shortcut", () => {
    // A lockdown that ate Ctrl+Enter would make the assessment unsendable,
    // and one that ate letters would make it unanswerable.
    const reported = install();
    for (const init of [{ key: "a" }, { key: "Enter" }, { key: "Enter", ctrlKey: true }, { key: "Backspace" }]) {
      expect(key(init).defaultPrevented, JSON.stringify(init)).toBe(false);
    }
    expect(reported).toEqual([]);
  });

  it("stops selection outside a field and allows it inside one", () => {
    install();
    const style = document.getElementById("proctoring-lockdown");
    expect(style?.textContent).toContain("user-select: none");
    expect(style?.textContent).toContain("input, textarea");
    expect(style?.textContent).toContain("user-select: text");
  });

  it("pushes a history navigation straight back and reports it", () => {
    const reported = install();
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(reported).toEqual(["history_navigation"]);
  });

  it("refuses the clipboard API and reports the attempt", async () => {
    const clipboard = { readText: () => Promise.resolve("secret"), writeText: () => Promise.resolve() };
    Object.defineProperty(window.navigator, "clipboard", { value: clipboard, configurable: true });
    const reported = install();
    await expect(window.navigator.clipboard.readText()).rejects.toThrow(/not available/i);
    expect(reported).toEqual(["clipboard_api"]);
    handle?.release();
    handle = null;
    // Released, the page is a page again: the original method is back.
    await expect(window.navigator.clipboard.readText()).resolves.toBe("secret");
  });

  it("warns on leaving the page", () => {
    const reported = install();
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(reported).toEqual(["leave_page"]);
  });

  it("removes every handler and its marker on release", () => {
    const reported = install();
    expect(handle?.installed()).toBe(true);
    handle?.release();
    expect(handle?.installed()).toBe(false);
    handle = null;
    expect(dispatch("copy").defaultPrevented).toBe(false);
    expect(key({ key: "p", ctrlKey: true }).defaultPrevented).toBe(false);
    expect(reported).toEqual([]);
    expect(document.getElementById("proctoring-lockdown")).toBeNull();
  });

  it("reports an attempt without ever seeing what was on the clipboard", () => {
    // The report says attempts occurred; it never says what the candidate
    // tried to copy. The callback's whole vocabulary is the action labels.
    const reported = install();
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData?: unknown };
    paste.clipboardData = { getData: vi.fn(() => "the answer") };
    document.body.dispatchEvent(paste);
    expect(reported).toEqual(["paste"]);
    expect((paste.clipboardData as { getData: ReturnType<typeof vi.fn> }).getData).not.toHaveBeenCalled();
  });
});

/**
 * The browser lockdown layer (proctoring spec 4.4).
 *
 * WHAT IT DOES, AND HONESTLY WHAT IT DOES NOT. Every action below is prevented
 * at the page level: the event is cancelled before the browser acts on it, the
 * shortcut is swallowed at keydown, text outside a field cannot be selected,
 * and a history navigation is pushed straight back. That reliably stops the
 * ORDINARY candidate from copying a question into a search engine or pasting
 * a prepared answer. It does not stop a determined candidate with developer
 * knowledge: a page cannot prevent the browser's own menus, an extension, a
 * second machine, or a script run from an already-open console. Nothing here,
 * in the shell or in the report claims otherwise, and the heartbeat, the
 * integrity self-check and server-side counting exist because this layer can
 * be removed by the person it runs for.
 *
 * Every refused action is reported through `onBlocked` with a short label.
 * The server records it as `BLOCKED_ACTION_ATTEMPTED`, Path C, so the report
 * can say attempts occurred without ever saying what was attempted to be
 * copied: no clipboard content, no key sequence, no text ever reaches this
 * callback.
 */

export type BlockedAction =
  | "copy"
  | "cut"
  | "paste"
  | "context_menu"
  | "drop"
  | "developer_tools"
  | "print"
  | "save"
  | "find"
  | "view_source"
  | "clipboard_api"
  | "history_navigation"
  | "leave_page";

export interface LockdownHandle {
  release(): void;
  /** Whether the lockdown's own marker is still on the page. A candidate who
   *  removed it from the console has defeated the layer; this is the cheapest
   *  honest check, and it is a check on the page, not on the person. */
  installed(): boolean;
}

export interface LockdownOptions {
  onBlocked: (action: BlockedAction) => void;
  document?: Document;
  window?: Window;
}

const STYLE_ID = "proctoring-lockdown";

const STYLE_TEXT = [
  // Text outside a field cannot be selected; the answer fields keep selection
  // so a candidate can still edit what they wrote.
  "body, body * { -webkit-user-select: none; user-select: none; }",
  "input, textarea, [contenteditable=\"true\"], [contenteditable=\"true\"] * { -webkit-user-select: text; user-select: text; }",
].join("\n");

/** The shortcut families spec 4.4 names, keyed on the letter after the
 *  modifier. Uppercase because `event.key` is upper when Shift is held. */
const DEVTOOLS_LETTERS = new Set(["I", "J", "C"]);
const PLAIN_SHORTCUTS: Record<string, BlockedAction> = {
  P: "print",
  S: "save",
  F: "find",
  U: "view_source",
};

export function classifyKeydown(event: KeyboardEvent): BlockedAction | null {
  if (event.key === "F12") return "developer_tools";
  const letter = event.key.length === 1 ? event.key.toUpperCase() : "";
  if (!letter) return null;
  const primary = event.ctrlKey || event.metaKey;
  // Ctrl+Shift+I/J/C on Windows and Linux; Cmd+Opt+I/J/C on a Mac.
  if (DEVTOOLS_LETTERS.has(letter) && ((primary && event.shiftKey) || (event.metaKey && event.altKey))) {
    return "developer_tools";
  }
  if (primary && !event.shiftKey && !event.altKey && letter in PLAIN_SHORTCUTS) {
    return PLAIN_SHORTCUTS[letter];
  }
  return null;
}

export function installLockdown(options: LockdownOptions): LockdownHandle {
  const doc = options.document ?? document;
  const win = options.window ?? window;
  const report = options.onBlocked;

  const cancel = (action: BlockedAction) => (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
    report(action);
  };
  const onCopy = cancel("copy");
  const onCut = cancel("cut");
  const onPaste = cancel("paste");
  const onContextMenu = cancel("context_menu");
  const onDrop = cancel("drop");
  // `dragover` must be cancelled for the browser to fire `drop` at all, and
  // it fires continuously while something is dragged; it is refused silently
  // and the single `drop` is what gets reported.
  const onDragOver = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const action = classifyKeydown(event);
    if (action === null) return;
    event.preventDefault();
    event.stopPropagation();
    report(action);
  };
  const onBeforeUnload = (event: BeforeUnloadEvent) => {
    // The browser shows its own leave-page prompt. If the candidate goes
    // through with it this event may never be sent; the heartbeat gap the
    // reload leaves is what the report will show instead.
    event.preventDefault();
    report("leave_page");
  };
  const onPopState = () => {
    win.history.pushState(null, "", win.location.href);
    report("history_navigation");
  };

  const capture = { capture: true } as const;
  doc.addEventListener("copy", onCopy, capture);
  doc.addEventListener("cut", onCut, capture);
  doc.addEventListener("paste", onPaste, capture);
  doc.addEventListener("contextmenu", onContextMenu, capture);
  doc.addEventListener("dragover", onDragOver, capture);
  doc.addEventListener("drop", onDrop, capture);
  doc.addEventListener("keydown", onKeyDown, capture);
  win.addEventListener("beforeunload", onBeforeUnload);
  win.history.pushState(null, "", win.location.href);
  win.addEventListener("popstate", onPopState);

  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLE_TEXT;
  doc.head.appendChild(style);

  // The asynchronous Clipboard API is a second door to the same data, so its
  // methods are replaced for the life of the lockdown. Each is replaced WHERE
  // IT ACTUALLY LIVES: a browser puts them on `Clipboard.prototype`, and
  // patching the prototype when the object carries its own would leave the
  // own property shadowing the patch. A script the candidate runs can restore
  // them; see the header.
  const clipboard = (win.navigator as Navigator & { clipboard?: Clipboard })
    .clipboard as unknown as Record<string, unknown> | undefined;
  const patched: Array<[Record<string, unknown>, string, unknown]> = [];
  if (clipboard) {
    const prototype = Object.getPrototypeOf(clipboard) as Record<string, unknown> | null;
    for (const method of ["readText", "writeText", "read", "write"]) {
      const target = Object.prototype.hasOwnProperty.call(clipboard, method)
        ? clipboard
        : prototype && typeof prototype[method] === "function"
          ? prototype
          : null;
      if (!target || typeof target[method] !== "function") continue;
      patched.push([target, method, target[method]]);
      target[method] = () => {
        report("clipboard_api");
        return Promise.reject(new Error("The clipboard is not available during a monitored assessment."));
      };
    }
  }

  return {
    installed: () => doc.getElementById(STYLE_ID) === style,
    release: () => {
      doc.removeEventListener("copy", onCopy, capture);
      doc.removeEventListener("cut", onCut, capture);
      doc.removeEventListener("paste", onPaste, capture);
      doc.removeEventListener("contextmenu", onContextMenu, capture);
      doc.removeEventListener("dragover", onDragOver, capture);
      doc.removeEventListener("drop", onDrop, capture);
      doc.removeEventListener("keydown", onKeyDown, capture);
      win.removeEventListener("beforeunload", onBeforeUnload);
      win.removeEventListener("popstate", onPopState);
      style.remove();
      for (const [target, method, original] of patched) target[method] = original;
    },
  };
}

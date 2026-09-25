// @vitest-environment jsdom
//
// The Monaco code editor: what it is configured with, and the clipboard fence
// around it (assessment spec 3: copy and paste are disabled everywhere and
// every attempt is logged).
//
// Monaco itself cannot run under jsdom, so `@monaco-editor/react` is replaced
// by `lib/assessment/monaco-test-double.tsx`, which records every decision the
// component hands the editor and renders a textarea INSIDE the component's
// host element. DOM events dispatched at that textarea therefore pass through
// the host's capture-phase listeners exactly as they would on the way to
// Monaco's own hidden textarea.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@monaco-editor/react", async () =>
  (await import("@/lib/assessment/monaco-test-double")).monacoReactModule()
);

import type { ProctoringFieldHooks } from "@/lib/assessment/contracts";
import {
  KEY_CODE,
  KEY_MOD,
  monacoDouble,
  resetMonacoDouble,
} from "@/lib/assessment/monaco-test-double";
import { resetMonacoSetupForTests } from "@/lib/assessment/monaco-setup";

import { CodeEditor, type CodeEditorProps } from "./code-editor";

function hooks(): ProctoringFieldHooks {
  return {
    onFieldFocus: vi.fn(),
    onFieldBlur: vi.fn(),
    onKeyDown: vi.fn(),
    onBlockedAction: vi.fn(),
    onOptionClick: vi.fn(),
    onScroll: vi.fn(),
  };
}

beforeEach(() => {
  resetMonacoDouble();
  resetMonacoSetupForTests();
});
afterEach(cleanup);

async function mount(overrides: Partial<CodeEditorProps> = {}) {
  const fieldHooks = overrides.fieldHooks ?? hooks();
  const onChange = overrides.onChange ?? vi.fn();
  const props: CodeEditorProps = {
    value: "print(1)\n",
    language: "python",
    onChange,
    fieldHooks,
    ariaLabel: "Your code",
    ...overrides,
  };
  const view = render(<CodeEditor {...props} />);
  const host = screen.getByTestId("code-editor");
  await waitFor(() => expect(host.getAttribute("data-load-state")).not.toBe("loading"));
  return { ...view, host, fieldHooks, onChange, props };
}

/** The kind each refused attempt was reported with, in order. */
function kinds(fieldHooks: ProctoringFieldHooks): unknown[] {
  return vi.mocked(fieldHooks.onBlockedAction).mock.calls.map((call) => call[0]);
}

function input(): HTMLTextAreaElement {
  return screen.getByTestId("monaco-input") as HTMLTextAreaElement;
}

function dispatch(event: Event): Event {
  input().dispatchEvent(event);
  return event;
}

function key(init: KeyboardEventInit): KeyboardEvent {
  return dispatch(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init })) as KeyboardEvent;
}

describe("configuration", () => {
  it("offers nothing that writes or explains code for the candidate", async () => {
    await mount();
    const options = monacoDouble.editors[0].options;
    expect(options).toMatchObject({
      quickSuggestions: false,
      suggestOnTriggerCharacters: false,
      wordBasedSuggestions: "off",
      snippetSuggestions: "none",
      tabCompletion: "off",
      parameterHints: { enabled: false },
      inlineSuggest: { enabled: false },
      hover: { enabled: "off" },
      codeLens: false,
      links: false,
      renderValidationDecorations: "off",
      autoClosingBrackets: "never",
      autoClosingQuotes: "never",
    });
  });

  it("turns off every clipboard and drag path the editor itself offers", async () => {
    await mount();
    expect(monacoDouble.editors[0].options).toMatchObject({
      contextmenu: false,
      dragAndDrop: false,
      dropIntoEditor: { enabled: false },
      pasteAs: { enabled: false },
      emptySelectionClipboard: false,
      selectionClipboard: false,
      editContext: false,
      minimap: { enabled: false },
    });
  });

  it("switches off the JavaScript and TypeScript language services before mounting", async () => {
    await mount({ language: "javascript" });
    // Two defaults (JavaScript, TypeScript), every feature off.
    expect(monacoDouble.modeConfigurations).toHaveLength(2);
    for (const configuration of monacoDouble.modeConfigurations) {
      expect(Object.values(configuration as Record<string, boolean>).every((on) => on === false)).toBe(true);
    }
    expect(monacoDouble.diagnosticsOptions).toContainEqual({
      noSemanticValidation: true,
      noSyntaxValidation: true,
      noSuggestionDiagnostics: true,
    });
  });

  it("draws in the product's own theme", async () => {
    await mount();
    expect(monacoDouble.definedThemes.map((theme) => theme.name)).toContain("vivekium");
    expect(monacoDouble.currentTheme).toBe("vivekium");
  });

  it("maps each language to its grammar, and anything unknown to plain text", async () => {
    await mount({ language: "cpp" });
    expect(input().getAttribute("data-language")).toBe("cpp");
    cleanup();
    await mount({ language: "brainfuck" });
    expect(input().getAttribute("data-language")).toBe("plaintext");
  });
});

describe("the clipboard fence on an editable editor", () => {
  it("refuses paste, copy, cut and drop and reports each attempt exactly once", async () => {
    const { fieldHooks } = await mount();
    for (const type of ["paste", "copy", "cut", "drop"]) {
      const event = dispatch(new Event(type, { bubbles: true, cancelable: true }));
      expect(event.defaultPrevented, type).toBe(true);
    }
    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(4);
    // Each attempt is reported with its kind, which is what the answer's
    // behaviour record and the report's sentence name.
    expect(kinds(fieldHooks)).toEqual(["paste", "copy", "cut", "drop"]);
  });

  it("cancels a drag silently so the browser will deliver the drop that is reported", async () => {
    const { fieldHooks } = await mount();
    const over = dispatch(new Event("dragover", { bubbles: true, cancelable: true }));
    expect(over.defaultPrevented).toBe(true);
    expect(fieldHooks.onBlockedAction).not.toHaveBeenCalled();
  });

  it("refuses a paste that arrives as input rather than as a clipboard event", async () => {
    const { fieldHooks } = await mount();
    const pasted = dispatch(
      new InputEvent("beforeinput", {
        bubbles: true,
        cancelable: true,
        inputType: "insertFromPaste",
      })
    );
    expect(pasted.defaultPrevented).toBe(true);
    const typed = dispatch(
      new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText" })
    );
    expect(typed.defaultPrevented).toBe(false);
    for (const inputType of ["deleteByCut", "insertFromDrop", "insertFromYank"]) {
      const refused = dispatch(
        new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType })
      );
      expect(refused.defaultPrevented, inputType).toBe(true);
    }
    // A cut is reported as a cut, not as the paste it is not.
    expect(kinds(fieldHooks)).toEqual(["paste", "cut", "drop", "paste"]);
  });

  it("refuses the clipboard keystrokes at keydown, reports once, and still records them", async () => {
    const { fieldHooks } = await mount();
    const refused = [
      key({ key: "v", ctrlKey: true }),
      key({ key: "c", metaKey: true }),
      key({ key: "x", ctrlKey: true }),
      key({ key: "V", ctrlKey: true, shiftKey: true }),
      key({ key: "Insert", shiftKey: true }),
      key({ key: "Insert", ctrlKey: true }),
      key({ key: "Delete", shiftKey: true }),
      // A Cyrillic layout: the character is not a V, the physical key is.
      key({ key: "м", code: "KeyV", ctrlKey: true }),
    ];
    for (const event of refused) expect(event.defaultPrevented, event.key).toBe(true);
    expect(kinds(fieldHooks)).toEqual([
      "paste",
      "copy",
      "cut",
      "paste",
      "paste",
      "copy",
      "cut",
      "paste",
    ]);
    // Every refused chord is still a keystroke in the answer's record.
    expect(fieldHooks.onKeyDown).toHaveBeenCalledTimes(refused.length);
  });

  it("leaves ordinary typing and AltGr characters alone", async () => {
    const { fieldHooks } = await mount();
    const typed = [
      key({ key: "v" }),
      key({ key: "Delete" }),
      // AltGr is reported as Ctrl+Alt; some layouts need it for ordinary characters.
      key({ key: "v", code: "KeyV", ctrlKey: true, altKey: true }),
    ];
    for (const event of typed) expect(event.defaultPrevented, event.key).toBe(false);
    expect(fieldHooks.onBlockedAction).not.toHaveBeenCalled();
    expect(vi.mocked(fieldHooks.onKeyDown).mock.calls[1][1]).toBe(true);
  });

  it("binds editor commands over the same keystrokes as the second fence", async () => {
    const { fieldHooks } = await mount();
    const commands = monacoDouble.editors[0].commands;
    const chords = [
      KEY_MOD.CtrlCmd | KEY_CODE.KeyC,
      KEY_MOD.CtrlCmd | KEY_CODE.KeyX,
      KEY_MOD.CtrlCmd | KEY_CODE.KeyV,
      KEY_MOD.CtrlCmd | KEY_MOD.Shift | KEY_CODE.KeyV,
      KEY_MOD.CtrlCmd | KEY_CODE.Insert,
      KEY_MOD.Shift | KEY_CODE.Insert,
      KEY_MOD.Shift | KEY_CODE.Delete,
    ];
    for (const chord of chords) {
      expect(commands.has(chord), `chord ${chord}`).toBe(true);
      commands.get(chord)?.();
    }
    expect(kinds(fieldHooks)).toEqual(["copy", "cut", "paste", "paste", "copy", "paste", "cut"]);
    // The command palette is swallowed and is not reported as an attempt.
    expect(commands.has(KEY_CODE.F1)).toBe(true);
    commands.get(KEY_CODE.F1)?.();
    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(chords.length);
  });

  it("stays in force while the field is disabled", async () => {
    const { fieldHooks } = await mount({ disabled: true });
    expect(monacoDouble.editors[0].options).toMatchObject({ readOnly: true, domReadOnly: true });
    const paste = dispatch(new Event("paste", { bubbles: true, cancelable: true }));
    expect(paste.defaultPrevented).toBe(true);
    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(1);
  });
});

describe("keystrokes, the shortcut and the other hooks", () => {
  it("records a keystroke with whether it deleted", async () => {
    const { fieldHooks } = await mount();
    key({ key: "Backspace" });
    key({ key: "a" });
    expect(vi.mocked(fieldHooks.onKeyDown).mock.calls.map((call) => call[1])).toEqual([true, false]);
  });

  it("hands Ctrl/Cmd+Enter to the caller and stops the editor seeing it", async () => {
    const onSubmitShortcut = vi.fn();
    const { fieldHooks } = await mount({ onSubmitShortcut });
    const pressed = key({ key: "Enter", ctrlKey: true });
    expect(pressed.defaultPrevented).toBe(true);
    expect(onSubmitShortcut).toHaveBeenCalledTimes(1);
    expect(fieldHooks.onKeyDown).toHaveBeenCalledTimes(1);
  });

  it("reports scrolling, focus and blur", async () => {
    const { fieldHooks } = await mount();
    const [listener] = monacoDouble.editors[0].scrollListeners;
    listener({ scrollTopChanged: true, scrollLeftChanged: false });
    listener({ scrollTopChanged: false, scrollLeftChanged: false });
    expect(fieldHooks.onScroll).toHaveBeenCalledTimes(1);
    fireEvent.focus(input());
    fireEvent.blur(input());
    expect(fieldHooks.onFieldFocus).toHaveBeenCalledTimes(1);
    expect(fieldHooks.onFieldBlur).toHaveBeenCalledTimes(1);
  });

  it("reports what the candidate typed", async () => {
    const { onChange } = await mount();
    fireEvent.change(input(), { target: { value: "print(2)\n" } });
    expect(onChange).toHaveBeenCalledWith("print(2)\n");
  });
});

describe("the read-only view", () => {
  it("installs no fence and binds no commands: a reader is not being assessed", async () => {
    await mount({ readOnly: true, onChange: undefined, fieldHooks: undefined });
    expect(screen.getByTestId("code-editor").getAttribute("data-readonly")).toBe("true");
    expect(input().readOnly).toBe(true);
    expect(monacoDouble.editors[0].commands.size).toBe(0);
    const copy = dispatch(new Event("copy", { bubbles: true, cancelable: true }));
    expect(copy.defaultPrevented).toBe(false);
  });
});

describe("a failed load", () => {
  it("tells a candidate and offers a reload instead of loading for ever", async () => {
    monacoDouble.loadResult = "fail";
    const { host } = await mount();
    expect(host.getAttribute("data-load-state")).toBe("failed");
    expect(screen.getByRole("alert").textContent).toContain("could not be loaded");
    expect(screen.getByRole("button", { name: /reload the page/i })).toBeTruthy();
    expect(screen.queryByTestId("monaco-input")).toBeNull();
  });

  it("shows a reader the code plainly and says why", async () => {
    monacoDouble.loadResult = "fail";
    await mount({ readOnly: true, value: "SELECT 1;", fieldHooks: undefined });
    const failed = screen.getByTestId("code-editor-failed");
    expect(failed.textContent).toContain("without highlighting");
    expect(failed.querySelector("pre")?.textContent).toBe("SELECT 1;");
  });
});

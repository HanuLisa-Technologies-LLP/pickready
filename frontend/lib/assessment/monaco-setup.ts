// How the coding editor's Monaco instance is loaded and configured.
//
// Everything here is plain data or a function of the Monaco API object, so it
// is testable without a browser: the editor component hands its instance in,
// and a test hands in a double.
//
// LOADED FROM THIS ORIGIN, NEVER A CDN. `@monaco-editor/react` defaults to a
// public CDN, which the Content-Security-Policy refuses. `configureMonacoLoader`
// points the AMD loader at the copy `scripts/copy-monaco.mjs` writes into
// `public/monaco/vs` on every build. It is called at module load, before any
// editor can mount, and the loader keeps one configuration for the page.
//
// NOTHING THAT WRITES THE ANSWER FOR THE CANDIDATE. The assessment spec forbids
// autocomplete that would solve the problem, so every suggestion surface is
// off: completion, snippets, parameter hints, inline suggestions, hovers,
// code lenses, light bulbs and inlay hints. Automatic bracket and quote
// closing stays off as it was in the editor this replaces. Diagnostics are
// off too: a red underline under a JavaScript type error is a hint engine by
// another name, and the four stdin/stdout grammars have no checker to be
// consistent with.

import { loader } from "@monaco-editor/react";
import type * as MonacoApi from "monaco-editor";

import type { BlockedFieldAction } from "@/lib/assessment/contracts";
import type { ClipboardAction } from "@/lib/assessment/editor-guard";

/** The Monaco API namespace, as the AMD loader hands it over. */
export type Monaco = typeof MonacoApi;
export type MonacoEditor = MonacoApi.editor.IStandaloneCodeEditor;
export type MonacoOptions = MonacoApi.editor.IStandaloneEditorConstructionOptions;

/** Where `scripts/copy-monaco.mjs` puts the AMD build, as a URL path. */
export const MONACO_VS_PATH = "/monaco/vs";

let configured = false;

/** Point the loader at the self-hosted copy. Idempotent. */
export function configureMonacoLoader(): void {
  if (configured) return;
  loader.config({ paths: { vs: MONACO_VS_PATH } });
  configured = true;
}

/**
 * The editor's language id for a coding language key.
 *
 * The four languages a deployment can offer today (python, java, cpp,
 * javascript: `backend/app/services/code_execution/languages.py`) and the
 * older keys a stored answer may still carry all have a Monaco grammar under
 * the same name. Anything else renders as plain text rather than under a
 * near-enough grammar: a C++ grammar colouring another language would mark
 * real code as broken, and a reader watching code light up wrongly is being
 * told something false about it.
 */
const MONACO_LANGUAGE_IDS = new Set([
  "python",
  "java",
  "cpp",
  "javascript",
  "typescript",
  "go",
  "csharp",
  "sql",
]);

export function monacoLanguageId(key: string): string {
  return MONACO_LANGUAGE_IDS.has(key) ? key : "plaintext";
}

/** The font stack the rest of the product reads (`app/layout.tsx` binds
 *  `--font-mono` to the self-hosted face). */
export const EDITOR_FONT_FAMILY =
  "var(--font-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/**
 * The editor options. `readOnly` is the recruiter's view of submitted code;
 * `disabled` is the candidate's field while an answer is being sent.
 */
export function editorOptions({
  readOnly,
  disabled,
  ariaLabel,
}: {
  readOnly: boolean;
  disabled: boolean;
  ariaLabel: string;
}): MonacoOptions {
  const locked = readOnly || disabled;
  return {
    readOnly: locked,
    domReadOnly: locked,
    ariaLabel,
    // One input path in every browser: the hidden textarea, whose paste,
    // drop and beforeinput events the guard in `editor-guard.ts` refuses.
    // The experimental EditContext path is Chromium only.
    editContext: false,
    automaticLayout: true,
    fontFamily: EDITOR_FONT_FAMILY,
    fontSize: 14,
    lineHeight: 22,
    lineNumbers: "on",
    scrollBeyondLastLine: false,
    wordWrap: "on",
    tabSize: 4,
    padding: { top: 12, bottom: 12 },
    minimap: { enabled: false },
    stickyScroll: { enabled: false },
    // Clipboard and drag: nothing in, nothing out.
    contextmenu: false,
    dragAndDrop: false,
    dropIntoEditor: { enabled: false },
    pasteAs: { enabled: false },
    emptySelectionClipboard: false,
    selectionClipboard: false,
    copyWithSyntaxHighlighting: false,
    // Nothing that writes or explains code for the candidate.
    quickSuggestions: false,
    suggestOnTriggerCharacters: false,
    wordBasedSuggestions: "off",
    snippetSuggestions: "none",
    tabCompletion: "off",
    acceptSuggestionOnEnter: "off",
    parameterHints: { enabled: false },
    inlineSuggest: { enabled: false },
    hover: { enabled: "off" },
    codeLens: false,
    lightbulb: { enabled: "off" as MonacoApi.editor.ShowLightbulbIconMode },
    inlayHints: { enabled: "off" },
    links: false,
    renderValidationDecorations: "off",
    autoClosingBrackets: "never",
    autoClosingQuotes: "never",
    autoSurround: "never",
    // A read-only view shows no cursor line: nothing there is being edited.
    renderLineHighlight: locked ? "none" : "line",
  };
}

/** The keybindings the editor refuses, as Monaco chords, each with the kind
 *  of attempt it is. The same set as `editor-guard.clipboardChord`, which
 *  refuses them one layer earlier. */
export function clipboardKeybindings(monaco: Monaco): Array<[number, ClipboardAction]> {
  const { KeyMod, KeyCode } = monaco;
  return [
    [KeyMod.CtrlCmd | KeyCode.KeyC, "copy"],
    [KeyMod.CtrlCmd | KeyCode.KeyX, "cut"],
    [KeyMod.CtrlCmd | KeyCode.KeyV, "paste"],
    [KeyMod.CtrlCmd | KeyMod.Shift | KeyCode.KeyV, "paste"],
    [KeyMod.CtrlCmd | KeyCode.Insert, "copy"],
    [KeyMod.Shift | KeyCode.Insert, "paste"],
    [KeyMod.Shift | KeyCode.Delete, "cut"],
  ];
}

/**
 * Register the editor-level half of the clipboard fence.
 *
 * The host guard refuses these chords at keydown before Monaco sees them, so
 * in practice these commands never run. They are the second fence, for any
 * path by which a keystroke reaches the editor without passing the host (an
 * editor-internal dispatch the guard does not know about). When one does run
 * it reports, and because the first fence stopped the event there is no path
 * on which both report one attempt.
 *
 * F1 opens the command palette, which lists the clipboard actions by name and
 * would run Paste through the Clipboard API; it is swallowed without a report
 * because opening a palette is not an attempt to paste.
 */
export function registerEditorCommands(
  editor: MonacoEditor,
  monaco: Monaco,
  onBlocked: (kind: BlockedFieldAction) => void
): void {
  for (const [chord, kind] of clipboardKeybindings(monaco)) {
    editor.addCommand(chord, () => onBlocked(kind));
  }
  editor.addCommand(monaco.KeyCode.F1, () => undefined);
}

let languageServicesDisabled = false;

/**
 * Switch off the JavaScript and TypeScript language services.
 *
 * They are the only languages Monaco ships a checker for, and the checker
 * runs in a worker: left on, a JavaScript answer would get squiggles, hovers
 * and completions the other three languages do not, and would start a
 * multi-megabyte worker to produce them. Global to the page, so done once.
 */
export function disableLanguageServices(monaco: Monaco): void {
  if (languageServicesDisabled) return;
  const off = {
    completionItems: false,
    hovers: false,
    documentSymbols: false,
    definitions: false,
    references: false,
    documentHighlights: false,
    rename: false,
    diagnostics: false,
    documentRangeFormattingEdits: false,
    signatureHelp: false,
    onTypeFormattingEdits: false,
    codeActions: false,
    inlayHints: false,
  };
  for (const defaults of [
    monaco.typescript.javascriptDefaults,
    monaco.typescript.typescriptDefaults,
  ]) {
    defaults.setModeConfiguration(off);
    defaults.setDiagnosticsOptions({
      noSemanticValidation: true,
      noSyntaxValidation: true,
      noSuggestionDiagnostics: true,
    });
  }
  languageServicesDisabled = true;
}

/** For tests: forget the page-level configuration so a fresh double can be
 *  configured again. */
export function resetMonacoSetupForTests(): void {
  configured = false;
  languageServicesDisabled = false;
}

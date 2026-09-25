// A stand-in for `@monaco-editor/react`, for tests. TEST SUPPORT ONLY.
//
// Monaco cannot run under jsdom: it measures glyphs, lays out its own view
// and loads itself through an AMD loader from `/monaco/vs`. What the product
// code does WITH it can be tested anyway, because every decision is made in
// our code and handed to the editor as data or callbacks: the options object,
// the commands bound over the clipboard keystrokes, the theme, the language
// services switched off, the value and the change callback, and the host
// element's own DOM listeners. This double records all of it.
//
// It renders a textarea standing in for the editor's hidden input, INSIDE the
// component's host element, so a DOM event dispatched at it travels through
// the host's capture-phase listeners exactly as a real keystroke into Monaco's
// textarea would. What it cannot show is Monaco's own internal event handling,
// and no test here claims to; that is verified in a real browser.
//
// Use it with:
//
//   vi.mock("@monaco-editor/react", async () =>
//     (await import("@/lib/assessment/monaco-test-double")).monacoReactModule());

import * as React from "react";

/** The KeyMod and KeyCode values Monaco itself uses (monaco.d.ts). */
export const KEY_MOD = { CtrlCmd: 2048, Shift: 1024, Alt: 512, WinCtrl: 256 } as const;
export const KEY_CODE = { Insert: 19, Delete: 20, KeyC: 33, KeyV: 52, KeyX: 54, F1: 59 } as const;

export interface RecordedEditor {
  commands: Map<number, () => void>;
  scrollListeners: Array<(event: { scrollTopChanged: boolean; scrollLeftChanged: boolean }) => void>;
  options: Record<string, unknown>;
  language: string | undefined;
  value: string | undefined;
}

export interface MonacoDoubleState {
  /** Every editor mounted since the last reset, in mount order. */
  editors: RecordedEditor[];
  definedThemes: Array<{ name: string; data: unknown }>;
  currentTheme: string | null;
  modeConfigurations: unknown[];
  diagnosticsOptions: unknown[];
  loaderConfigs: unknown[];
  /** Whether `loader.init()` resolves (the editor loads) or rejects. */
  loadResult: "ok" | "fail";
}

export const monacoDouble: MonacoDoubleState = {
  editors: [],
  definedThemes: [],
  currentTheme: null,
  modeConfigurations: [],
  diagnosticsOptions: [],
  loaderConfigs: [],
  loadResult: "ok",
};

/**
 * The light-theme token values the editor theme reads, set inline on the
 * document root. jsdom loads no stylesheet, so without these every token
 * reads as empty and the theme builder refuses, which is its correct
 * behaviour in a browser whose tokens have gone missing.
 */
const LIGHT_TOKENS: Record<string, string> = {
  ink: "212 55% 7%",
  surface: "0 0% 100%",
  muted: "212 40% 96%",
  "border-token": "212 33% 92%",
  "navy-50": "210 58% 96%",
  "navy-100": "210 58% 90%",
  "navy-400": "210 80% 38%",
  "navy-500": "210 75% 26%",
  "teal-100": "175 60% 88%",
  "teal-600": "175 84% 32%",
  "teal-700": "175 84% 23%",
  "teal-900": "175 84% 11%",
};

export function installThemeTokens(): void {
  for (const [token, value] of Object.entries(LIGHT_TOKENS)) {
    document.documentElement.style.setProperty(`--${token}`, value);
  }
}

export function resetMonacoDouble(): void {
  installThemeTokens();
  monacoDouble.editors = [];
  monacoDouble.definedThemes = [];
  monacoDouble.currentTheme = null;
  monacoDouble.modeConfigurations = [];
  monacoDouble.diagnosticsOptions = [];
  monacoDouble.loaderConfigs = [];
  monacoDouble.loadResult = "ok";
}

function languageDefaults() {
  return {
    setModeConfiguration: (configuration: unknown) =>
      monacoDouble.modeConfigurations.push(configuration),
    setDiagnosticsOptions: (options: unknown) => monacoDouble.diagnosticsOptions.push(options),
  };
}

/** The Monaco API object, as much of it as the product calls. */
export const fakeMonaco = {
  KeyMod: KEY_MOD,
  KeyCode: KEY_CODE,
  editor: {
    defineTheme: (name: string, data: unknown) => monacoDouble.definedThemes.push({ name, data }),
    setTheme: (name: string) => {
      monacoDouble.currentTheme = name;
    },
    remeasureFonts: () => undefined,
  },
  typescript: {
    javascriptDefaults: languageDefaults(),
    typescriptDefaults: languageDefaults(),
  },
};

interface EditorDoubleProps {
  value?: string;
  language?: string;
  height?: string;
  options?: Record<string, unknown>;
  loading?: React.ReactNode;
  beforeMount?: (monaco: typeof fakeMonaco) => void;
  onMount?: (editor: unknown, monaco: typeof fakeMonaco) => void;
  onChange?: (value: string | undefined) => void;
}

function EditorDouble({
  value,
  language,
  options = {},
  beforeMount,
  onMount,
  onChange,
}: EditorDoubleProps) {
  const record = React.useRef<RecordedEditor | null>(null);
  if (record.current === null) {
    record.current = {
      commands: new Map(),
      scrollListeners: [],
      options,
      language,
      value,
    };
  }
  record.current.options = options;
  record.current.language = language;
  record.current.value = value;

  React.useEffect(() => {
    const recorded = record.current as RecordedEditor;
    monacoDouble.editors.push(recorded);
    beforeMount?.(fakeMonaco);
    const editor = {
      addCommand: (keybinding: number, handler: () => void) => {
        recorded.commands.set(keybinding, handler);
        return String(keybinding);
      },
      onDidScrollChange: (
        listener: (event: { scrollTopChanged: boolean; scrollLeftChanged: boolean }) => void
      ) => {
        recorded.scrollListeners.push(listener);
        return { dispose: () => undefined };
      },
    };
    onMount?.(editor, fakeMonaco);
    // Mount once, like the real wrapper.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <textarea
      data-testid="monaco-input"
      data-language={language}
      aria-label={String(options.ariaLabel ?? "")}
      readOnly={Boolean(options.readOnly)}
      value={value ?? ""}
      onChange={(event) => onChange?.(event.target.value)}
    />
  );
}

/** The module `vi.mock("@monaco-editor/react", ...)` should return. */
export function monacoReactModule() {
  const loader = {
    config: (configuration: unknown) => monacoDouble.loaderConfigs.push(configuration),
    init: () => {
      let cancelled = false;
      const promise = new Promise<typeof fakeMonaco>((resolve, reject) => {
        queueMicrotask(() => {
          if (cancelled) {
            reject({ type: "cancelation", msg: "operation is manually canceled" });
          } else if (monacoDouble.loadResult === "ok") {
            resolve(fakeMonaco);
          } else {
            reject(new Error("Script error for vs/editor/editor.main"));
          }
        });
      }) as Promise<typeof fakeMonaco> & { cancel: () => void };
      promise.cancel = () => {
        cancelled = true;
      };
      return promise;
    },
  };
  return { __esModule: true, default: EditorDouble, Editor: EditorDouble, loader };
}

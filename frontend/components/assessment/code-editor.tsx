"use client";

// The code editor: Monaco, for the coding format and for the recruiter's
// read-only view of what was submitted.
//
// SELF-HOSTED. Monaco is fetched at runtime by its own AMD loader from
// `/monaco/vs`, which `scripts/copy-monaco.mjs` fills from the pinned package
// on every build (`lib/assessment/monaco-setup.ts` explains why never a CDN).
// Only the small React wrapper is in the page bundle, so the editor's several
// megabytes are downloaded only when an editor actually mounts: on a coding
// question, or on a transcript that shows submitted code.
//
// CLIPBOARD AND DROP ARE REFUSED IN TWO LAYERS on an editable editor, both
// reporting through `fieldHooks.onBlockedAction()`: DOM listeners on this
// component's host in the capture phase (`lib/assessment/editor-guard.ts`),
// and editor commands bound over the clipboard keystrokes
// (`monaco-setup.registerEditorCommands`). The read-only view installs
// neither: a recruiter reading submitted code is not being assessed.
//
// KEYSTROKES ARE RECORDED ON THE HOST, IN THE CAPTURE PHASE, NOT INSIDE THE
// EDITOR. A keydown reaches the host before the editor sees it, so
// Ctrl/Cmd+Enter can be stopped here and the editor never inserts the line
// break that a handler running after it would have to undo. And the recorder
// does not depend on the editor's internal event routing, which is what a
// keystroke recorder must not be at the mercy of: the recording is proctoring
// evidence, and a library upgrade that reorders its own dispatch would
// silently stop it. The recorder is registered BEFORE the clipboard guard on
// the same element, so a refused Ctrl+V is still recorded as a keystroke.
//
// A FAILED LOAD IS SAID OUT LOUD. The wrapper's own behaviour when the
// loader fails is to log to the console and show its loading text for ever,
// which on a timed question is a candidate watching a placeholder while the
// clock runs. The load is tracked here and a failure replaces the placeholder
// with a sentence and a reload control.

import * as React from "react";
import Editor, { loader } from "@monaco-editor/react";
import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ProctoringFieldHooks } from "@/lib/assessment/contracts";
import { installEditorGuard } from "@/lib/assessment/editor-guard";
import { isDeletionKey, isSubmitShortcut } from "@/lib/assessment/field-events";
import {
  configureMonacoLoader,
  disableLanguageServices,
  editorOptions,
  monacoLanguageId,
  registerEditorCommands,
  type Monaco,
  type MonacoEditor,
} from "@/lib/assessment/monaco-setup";
import {
  applyMonacoTheme,
  MONACO_THEME_NAME,
  watchPageTheme,
} from "@/lib/assessment/monaco-theme";

configureMonacoLoader();

type LoadState = "loading" | "ready" | "failed";

export interface CodeEditorProps {
  value: string;
  language: string;
  /** Omitted for a read-only view. */
  onChange?: (code: string) => void;
  readOnly?: boolean;
  disabled?: boolean;
  fieldHooks?: ProctoringFieldHooks;
  /** Called on Ctrl/Cmd+Enter inside the editor, after the keystroke has
   *  been recorded. The caller decides what the shortcut means. */
  onSubmitShortcut?: () => void;
  ariaLabel: string;
  /** Any CSS height. The editor does not size itself to its content. */
  height?: string;
  className?: string;
}

export function CodeEditor({
  value,
  language,
  onChange,
  readOnly = false,
  disabled = false,
  fieldHooks,
  onSubmitShortcut,
  ariaLabel,
  height = "18rem",
  className,
}: CodeEditorProps) {
  const host = React.useRef<HTMLDivElement | null>(null);
  // The handlers read the latest props through a ref so the editor, which is
  // expensive to build, is never rebuilt because a callback identity changed.
  const latest = React.useRef({ onChange, fieldHooks, onSubmitShortcut });
  latest.current = { onChange, fieldHooks, onSubmitShortcut };
  const releaseTheme = React.useRef<(() => void) | null>(null);
  const [loadState, setLoadState] = React.useState<LoadState>("loading");

  React.useEffect(() => {
    const init = loader.init();
    init.then(
      () => setLoadState("ready"),
      (error: unknown) => {
        // Unmounting cancels this wrapper around the loader's shared promise;
        // that is not a failure of the load.
        if ((error as { type?: string } | null)?.type === "cancelation") return;
        setLoadState("failed");
      }
    );
    return () => init.cancel();
  }, []);

  React.useEffect(() => () => releaseTheme.current?.(), []);

  // The recorder and the clipboard guard, on the host, in the capture phase.
  // Only on an editable editor: nothing is being answered in the read-only
  // view.
  React.useEffect(() => {
    const element = host.current;
    if (!element || readOnly) return;
    const record = (event: KeyboardEvent) => {
      const { fieldHooks: hooks, onSubmitShortcut: submit } = latest.current;
      hooks?.onKeyDown(event.timeStamp, isDeletionKey(event.key));
      if (submit && isSubmitShortcut(event)) {
        event.preventDefault();
        event.stopPropagation();
        submit();
      }
    };
    element.addEventListener("keydown", record, { capture: true });
    const removeGuard = installEditorGuard(element, {
      onBlocked: (kind) => latest.current.fieldHooks?.onBlockedAction(kind),
    });
    return () => {
      element.removeEventListener("keydown", record, { capture: true });
      removeGuard();
    };
  }, [readOnly]);

  const options = React.useMemo(
    () => editorOptions({ readOnly, disabled, ariaLabel }),
    [readOnly, disabled, ariaLabel]
  );

  const beforeMount = React.useCallback((monaco: Monaco) => {
    disableLanguageServices(monaco);
    applyMonacoTheme(monaco);
  }, []);

  const onMount = React.useCallback(
    (editor: MonacoEditor, monaco: Monaco) => {
      if (!readOnly) {
        registerEditorCommands(editor, monaco, (kind) =>
          latest.current.fieldHooks?.onBlockedAction(kind)
        );
      }
      releaseTheme.current?.();
      releaseTheme.current = watchPageTheme(monaco);
      editor.onDidScrollChange((event) => {
        if (event.scrollTopChanged || event.scrollLeftChanged) {
          latest.current.fieldHooks?.onScroll();
        }
      });
      // The mono face is self-hosted and swaps in after first paint; Monaco
      // measures glyph widths once, so it measures again when it arrives or
      // the cursor drifts off the characters.
      void document.fonts?.ready.then(() => monaco.editor.remeasureFonts());
    },
    [readOnly]
  );

  const locked = readOnly || disabled;

  return (
    <div
      ref={host}
      className={className}
      data-testid="code-editor"
      data-language={language}
      data-readonly={locked ? "true" : "false"}
      data-load-state={loadState}
      // `onFocus`/`onBlur` are focusin/focusout in React, which bubble from
      // the editor's input element, so one pair on the host covers it.
      onFocus={() => latest.current.fieldHooks?.onFieldFocus()}
      onBlur={() => latest.current.fieldHooks?.onFieldBlur()}
    >
      {loadState === "failed" ? (
        <EditorLoadFailure readOnly={readOnly} value={value} height={height} />
      ) : (
        <div className="border border-input">
          <Editor
            // One editor per language: switching language starts a fresh
            // undo history, so Ctrl+Z can never bring another language's
            // code back under this one's name.
            key={language}
            value={value}
            language={monacoLanguageId(language)}
            theme={MONACO_THEME_NAME}
            height={height}
            options={options}
            beforeMount={beforeMount}
            onMount={onMount}
            onChange={(next) => {
              if (!locked) latest.current.onChange?.(next ?? "");
            }}
            loading={
              <p className="text-sm" data-testid="code-editor-loading">
                Loading the code editor
              </p>
            }
          />
        </div>
      )}
    </div>
  );
}

function EditorLoadFailure({
  readOnly,
  value,
  height,
}: {
  readOnly: boolean;
  value: string;
  height: string;
}) {
  if (readOnly) {
    // The code itself is the evidence; it is shown plainly rather than not
    // at all, and the reader is told why it looks different.
    return (
      <div className="space-y-2" data-testid="code-editor-failed">
        <p className="text-xs" role="status">
          The code viewer could not be loaded, so the code is shown without highlighting.
        </p>
        <pre
          className="overflow-auto border border-input bg-surface p-3 font-mono text-sm"
          style={{ maxHeight: height }}
        >
          {value}
        </pre>
      </div>
    );
  }
  return (
    <div
      className="flex flex-col items-start gap-3 border border-input bg-surface p-4"
      style={{ minHeight: height }}
      data-testid="code-editor-failed"
      role="alert"
    >
      <p className="text-sm">
        The code editor could not be loaded. Reload the page to try again.
      </p>
      <Button type="button" variant="outline" onClick={() => window.location.reload()}>
        <RotateCw className="h-4 w-4" aria-hidden="true" />
        Reload the page
      </Button>
    </div>
  );
}

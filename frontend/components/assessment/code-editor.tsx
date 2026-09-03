"use client";

// The code editor: CodeMirror 6, for the coding format and for the recruiter's
// read-only view of what was submitted.
//
// WHAT IS DELIBERATELY NOT HERE
// -----------------------------
// No autocompletion. The `codemirror` meta-package's `basicSetup` bundles
// `@codemirror/autocomplete`, which completes identifiers and closes brackets,
// and a completion popup on an assessment is a hint engine (assessment spec
// 2.5: "no autocomplete that would solve the problem for them"). The
// extensions are listed one by one instead so that nothing arrives by bundle.
//
// PASTE AND DROP ARE REFUSED AT THE EDITOR'S OWN DOM HANDLERS, in addition to
// the lockdown layer the proctoring shell installs on the document. The two do
// different jobs: the lockdown emits the session-level blocked-action event
// and this handler counts the attempt against the answer it was aimed at. Both
// fire on one attempt, deliberately. This stops the ordinary candidate; it
// does not stop a determined one with developer knowledge, and nothing here
// claims otherwise.
//
// THE HIGHLIGHT STYLE IS THE BRAND'S OWN. CodeMirror's default style colours
// keywords purple, which DESIGN.md forbids anywhere, and its comment colour is
// a grey, which the product's text never is. Every colour below is a token
// from globals.css, so the editor follows the theme swap like every other
// surface.

import * as React from "react";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { Compartment, EditorState } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";

import type { ProctoringFieldHooks } from "@/lib/assessment/contracts";
import { languageSupport } from "@/lib/assessment/coding-languages";
import { isDeletionKey, isSubmitShortcut } from "@/lib/assessment/field-events";

const brandHighlight = HighlightStyle.define([
  {
    tag: [
      tags.keyword,
      tags.controlKeyword,
      tags.operatorKeyword,
      tags.definitionKeyword,
      tags.moduleKeyword,
    ],
    color: "hsl(var(--navy-400))",
    fontWeight: "600",
  },
  // Teal is evidence: a literal is the one thing in a program that states a
  // fact rather than a structure.
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "hsl(var(--teal-700))" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "hsl(var(--navy-500))" },
  // Comments in ink, italic. Never dimmed: text is never grey.
  { tag: [tags.comment, tags.lineComment, tags.blockComment, tags.docComment], fontStyle: "italic" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], fontWeight: "600" },
  { tag: [tags.typeName, tags.className, tags.namespace], color: "hsl(var(--teal-900))" },
  { tag: tags.invalid, textDecoration: "underline" },
]);

const theme = EditorView.theme({
  "&": {
    backgroundColor: "hsl(var(--surface))",
    color: "hsl(var(--ink))",
    fontSize: "0.875rem",
    border: "1px solid hsl(var(--input))",
  },
  "&.cm-focused": {
    outline: "none",
    borderColor: "hsl(var(--navy-600))",
    boxShadow: "0 0 0 2px hsl(var(--ring) / 0.3)",
  },
  ".cm-scroller": {
    fontFamily:
      "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    lineHeight: "1.6",
    overflow: "auto",
    maxHeight: "60vh",
  },
  ".cm-content": { caretColor: "hsl(var(--ink))", padding: "0.75rem 0" },
  ".cm-gutters": {
    backgroundColor: "hsl(var(--muted))",
    color: "hsl(var(--ink))",
    borderRight: "1px solid hsl(var(--border))",
  },
  ".cm-activeLine": { backgroundColor: "hsl(var(--navy-50))" },
  ".cm-activeLineGutter": { backgroundColor: "hsl(var(--navy-100))" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
    backgroundColor: "hsl(var(--navy-100))",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "hsl(var(--ink))" },
  ".cm-matchingBracket": {
    backgroundColor: "hsl(var(--teal-100))",
    outline: "1px solid hsl(var(--teal-600))",
  },
});

export interface CodeEditorProps {
  value: string;
  language: string;
  /** Omitted for a read-only view. */
  onChange?: (code: string) => void;
  readOnly?: boolean;
  disabled?: boolean;
  fieldHooks?: ProctoringFieldHooks;
  onSubmitShortcut?: () => void;
  ariaLabel: string;
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
  className,
}: CodeEditorProps) {
  const host = React.useRef<HTMLDivElement | null>(null);
  const view = React.useRef<EditorView | null>(null);
  const languageCompartment = React.useRef(new Compartment());
  const editableCompartment = React.useRef(new Compartment());
  // The handlers read the latest props through a ref so the view, which is
  // expensive to build, is created once and never rebuilt because a callback
  // identity changed.
  const latest = React.useRef({ onChange, fieldHooks, onSubmitShortcut });
  latest.current = { onChange, fieldHooks, onSubmitShortcut };
  // What the editor last reported, so an echo of its own change is not
  // dispatched back into it as an external update.
  const lastEmitted = React.useRef(value);

  const editable = !readOnly && !disabled;

  React.useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: lastEmitted.current,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        highlightActiveLine(),
        syntaxHighlighting(brandHighlight),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        EditorView.lineWrapping,
        theme,
        EditorView.contentAttributes.of({ "aria-label": ariaLabel }),
        languageCompartment.current.of(languageSupport(language) ?? []),
        editableCompartment.current.of([
          EditorView.editable.of(editable),
          EditorState.readOnly.of(!editable),
        ]),
        EditorView.domEventHandlers({
          paste(event) {
            event.preventDefault();
            latest.current.fieldHooks?.onBlockedAction();
            return true;
          },
          drop(event) {
            event.preventDefault();
            latest.current.fieldHooks?.onBlockedAction();
            return true;
          },
          dragover(event) {
            // Without this the browser never fires `drop`, so the attempt
            // would be neither refused nor counted.
            event.preventDefault();
            return true;
          },
        }),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          const code = update.state.doc.toString();
          lastEmitted.current = code;
          latest.current.onChange?.(code);
        }),
      ],
    });
    const created = new EditorView({ state, parent: host.current });
    view.current = created;
    // `scroll` does not bubble, so the document-level handlers register on
    // the content element never see it; the scrolling element is listened to
    // directly.
    const onScroll = () => latest.current.fieldHooks?.onScroll();
    created.scrollDOM.addEventListener("scroll", onScroll);
    return () => {
      created.scrollDOM.removeEventListener("scroll", onScroll);
      created.destroy();
      view.current = null;
    };
    // The view is built once. Language, editability and value are pushed in
    // by the effects below through compartments and transactions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    view.current?.dispatch({
      effects: languageCompartment.current.reconfigure(languageSupport(language) ?? []),
    });
  }, [language]);

  React.useEffect(() => {
    view.current?.dispatch({
      effects: editableCompartment.current.reconfigure([
        EditorView.editable.of(editable),
        EditorState.readOnly.of(!editable),
      ]),
    });
  }, [editable]);

  React.useEffect(() => {
    const current = view.current;
    if (!current || value === lastEmitted.current) return;
    // An external value (a restored draft, a cleared field, a language
    // reset): replace the whole document rather than diffing, because the
    // editor's own history is what a candidate would undo through and an
    // external replacement is one step.
    lastEmitted.current = value;
    current.dispatch({
      changes: { from: 0, to: current.state.doc.length, insert: value },
    });
  }, [value]);

  // KEYSTROKES ARE RECORDED ON THE HOST, IN THE CAPTURE PHASE, NOT INSIDE THE
  // EDITOR'S OWN HANDLER PIPELINE.
  //
  // Two reasons, and the second is the load-bearing one. A keydown reaches
  // the host before the editor sees it, so Ctrl/Cmd+Enter can be stopped
  // here and the editor never inserts the line break that a handler running
  // after it would have to undo. And the capture handler does not depend on
  // the editor's internal event routing, which is what a keystroke recorder
  // must not be at the mercy of: the recording is proctoring evidence, and a
  // library upgrade that reorders its own dispatch would silently stop it.
  // Paste and drop stay on the editor's own DOM handlers, where they can
  // refuse the editor's default behaviour rather than race it.
  const onKeyDownCapture = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const { fieldHooks: hooks, onSubmitShortcut: submit } = latest.current;
    hooks?.onKeyDown(event.timeStamp, isDeletionKey(event.key));
    if (submit && isSubmitShortcut(event)) {
      event.preventDefault();
      event.stopPropagation();
      submit();
    }
  };

  return (
    <div
      ref={host}
      className={className}
      data-testid="code-editor"
      data-language={language}
      data-readonly={readOnly || disabled ? "true" : "false"}
      onKeyDownCapture={onKeyDownCapture}
      // `onFocus`/`onBlur` are focusin/focusout in React, which bubble from
      // the content element, so one pair on the host covers the editor.
      onFocus={() => latest.current.fieldHooks?.onFieldFocus()}
      onBlur={() => latest.current.fieldHooks?.onFieldBlur()}
    />
  );
}

// The CodeMirror language support for each coding language the server permits.
//
// Keyed by the ids in `services/assessment_formats/types.CODING_LANGUAGES`.
// A language without a grammar package on the approved list (C#) and plain
// text get NO highlighting rather than a near-enough grammar: a C++ grammar
// colouring C# would mark real code as errors, and a candidate watching their
// own code light up red is being told something false about it.

import { cpp } from "@codemirror/lang-cpp";
import { go } from "@codemirror/lang-go";
import { java } from "@codemirror/lang-java";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import type { LanguageSupport } from "@codemirror/language";

const SUPPORT: Record<string, () => LanguageSupport> = {
  python: () => python(),
  javascript: () => javascript(),
  typescript: () => javascript({ typescript: true }),
  java: () => java(),
  go: () => go(),
  cpp: () => cpp(),
  sql: () => sql(),
};

/** The grammar for `language`, or null when the editor should stay plain. */
export function languageSupport(language: string): LanguageSupport | null {
  const factory = SUPPORT[language];
  return factory ? factory() : null;
}

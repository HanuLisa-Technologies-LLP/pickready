// The words a candidate reads about each coding language are the backend's.
//
// `backend/app/services/code_execution/languages.py` is the registry of the
// languages a deployment can offer: each `LanguageSpec` carries the label a
// person reads and `source_hint`, the sentence about how the program is run.
// The Java hint in particular is a rule the sandbox enforces (code in any
// class other than Main does not compile there), so a frontend copy that
// drifted from it would tell a candidate something the runner then punishes.
// This reads the Python source and compares, in both directions.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

import { CODING_LANGUAGE_LABELS } from "./answers";
import { CODING_LANGUAGE_HINTS } from "./coding";
import { monacoLanguageId } from "./monaco-setup";

const REPO = fileURLToPath(new URL("../../..", import.meta.url));

interface Spec {
  key: string;
  label: string;
  sourceHint: string;
}

/** A Python string expression: one literal, or adjacent literals inside
 *  parentheses, which Python concatenates. */
function pythonString(expression: string): string {
  const parts = [...expression.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((match) => match[1]);
  if (parts.length === 0) throw new Error(`not a string expression: ${expression}`);
  return parts.join("");
}

function backendSpecs(): Spec[] {
  const source = readFileSync(
    join(REPO, "backend", "app", "services", "code_execution", "languages.py"),
    "utf8"
  );
  const registry = source.slice(source.indexOf("LANGUAGE_SPECS"));
  const specs: Spec[] = [];
  for (const block of registry.matchAll(/LanguageSpec\(([\s\S]*?)\n    \),/g)) {
    const body = block[1];
    const field = (name: string) => {
      const match = new RegExp(`${name}=(\\([\\s\\S]*?\\)|"(?:[^"\\\\]|\\\\.)*")`).exec(body);
      if (!match) throw new Error(`LanguageSpec without ${name}: ${body}`);
      return pythonString(match[1]);
    };
    specs.push({ key: field("key"), label: field("label"), sourceHint: field("source_hint") });
  }
  return specs;
}

describe("the coding languages", () => {
  const specs = backendSpecs();

  it("reads the registry at all", () => {
    // A parser that silently matched nothing would pass every check below.
    expect(specs.map((spec) => spec.key)).toEqual(["python", "java", "cpp", "javascript"]);
  });

  it("names each language exactly as the backend does", () => {
    for (const spec of specs) {
      expect(CODING_LANGUAGE_LABELS[spec.key], spec.key).toBe(spec.label);
    }
  });

  it("tells the candidate how each program is run in the backend's words, and no others", () => {
    expect(Object.keys(CODING_LANGUAGE_HINTS).sort()).toEqual(specs.map((spec) => spec.key).sort());
    for (const spec of specs) {
      expect(CODING_LANGUAGE_HINTS[spec.key], spec.key).toBe(spec.sourceHint);
    }
  });

  it("highlights every offered language under its own grammar", () => {
    for (const spec of specs) expect(monacoLanguageId(spec.key)).toBe(spec.key);
  });
});

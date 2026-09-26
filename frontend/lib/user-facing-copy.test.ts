// The words a reader sees name the product's own concepts, and only those.
//
// CONTRACT v4 item 5 (the Vivekium release): recruiter-visible copy uses JD,
// SWOT, Skills, AI Match, Tatva Assessment, PRISM Report and Proctoring Report.
// It does not say PPI (the retired name of the Tatva Assessment), matrix,
// framework or matching categories (the internal shapes behind Skills), AI
// Score (the pre-assessment check is the AI Match), and it does not explain a
// retake, because there is none.
//
// IDENTIFIERS ARE NOT COPY. `PPIReportModal`, `ppi-report-modal.tsx`, the
// `/framework` routes and the `ai_score` payload key keep their names: routes
// are quoted in issued links and keys are read from stored immutable reports.
// So this sweep reads the TypeScript AST rather than the raw text, and checks
// only what can reach a screen:
//
//   * JSX text;
//   * string and template literals that read as prose (a letter and a space),
//     outside import specifiers, `className`, console calls and API paths.
//
// Comments are never read, which is what lets the files that document the
// old names keep documenting them. The backend half is
// `backend/tests/test_user_facing_copy_names.py`.

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "..");
const SWEPT_DIRS = ["app", "components", "lib"];

export const FORBIDDEN: RegExp[] = [
  /\bPPI\b/,
  /\bretak(e|es|en|ing)\b/i,
  /\bmatri(x|ces)\b/i,
  /\bframeworks?\b/i,
  /\bmatching categor(y|ies)\b/i,
  /\bAI Scores?\b/i,
];

/** Attributes whose value is never read by a person. */
const NON_COPY_ATTRIBUTES = new Set(["className", "key", "href", "id", "src", "type", "name"]);
/** Calls whose string arguments are addresses or operator output, not copy. */
const NON_COPY_CALLS = new Set([
  "apiGet",
  "apiPost",
  "apiPut",
  "apiPatch",
  "apiDelete",
  "fetch",
  "log",
  "warn",
  "error",
  "info",
  "debug",
]);

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "node_modules" || entry.startsWith(".")) continue;
      found.push(...sourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) && !entry.endsWith(".d.ts")) {
      found.push(full);
    }
  }
  return found;
}

function isNonCopyContext(node: ts.Node): boolean {
  for (let current: ts.Node | undefined = node.parent; current; current = current.parent) {
    if (ts.isImportDeclaration(current) || ts.isExportDeclaration(current)) return true;
    if (ts.isJsxAttribute(current)) {
      return NON_COPY_ATTRIBUTES.has(current.name.getText());
    }
    if (ts.isCallExpression(current)) {
      const callee = current.expression;
      const name = ts.isPropertyAccessExpression(callee)
        ? callee.name.text
        : ts.isIdentifier(callee)
          ? callee.text
          : "";
      if (NON_COPY_CALLS.has(name)) return true;
    }
    // A string that is itself a module path in a dynamic import.
    if (ts.isImportTypeNode(current)) return true;
  }
  return false;
}

/** Every piece of text in a file that can reach a screen, with its line. */
export function copyIn(fileName: string, text: string): Array<{ line: number; text: string }> {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
  const found: Array<{ line: number; text: string }> = [];
  const push = (node: ts.Node, value: string) => {
    found.push({ line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1, text: value });
  };
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) {
      const value = node.text.replace(/\s+/g, " ").trim();
      if (value) push(node, value);
    } else if (
      (ts.isStringLiteral(node) ||
        ts.isNoSubstitutionTemplateLiteral(node) ||
        ts.isTemplateHead(node) ||
        ts.isTemplateMiddle(node) ||
        ts.isTemplateTail(node)) &&
      /[A-Za-z]/.test(node.text) &&
      / /.test(node.text) &&
      !isNonCopyContext(node)
    ) {
      push(node, node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

function hits(value: string): string[] {
  return FORBIDDEN.flatMap((pattern) => {
    const match = value.match(pattern);
    return match ? [match[0]] : [];
  });
}

describe("user-facing copy names only the product's concepts", () => {
  it("finds no retired name in any text that can reach a screen", () => {
    const offending: string[] = [];
    let swept = 0;
    for (const dir of SWEPT_DIRS) {
      for (const file of sourceFiles(path.join(ROOT, dir))) {
        for (const piece of copyIn(file, readFileSync(file, "utf8"))) {
          swept += 1;
          for (const hit of hits(piece.text)) {
            offending.push(`${path.relative(ROOT, file)}:${piece.line}: ${hit}`);
          }
        }
      }
    }
    // A sweep over nothing passes; that is the failure it exists to catch.
    expect(swept).toBeGreaterThan(3000);
    expect(offending).toEqual([]);
  });

  it("reads copy and skips identifiers and comments", () => {
    const sample = [
      'import { PPIReportModal } from "@/components/ppi-report-modal";',
      "// The PPI matrix was renamed.",
      "export function View() {",
      '  const url = apiGet("/api/v2/jobs/1/framework and more");',
      '  return <div className="matrix grid">Read the PPI Assessment Report</div>;',
      "}",
      'const label = "Save the matrix first";',
    ].join("\n");
    const pieces = copyIn("sample.tsx", sample).map((piece) => piece.text);
    expect(pieces).toContain("Read the PPI Assessment Report");
    expect(pieces).toContain("Save the matrix first");
    expect(pieces.join(" ")).not.toContain("renamed");
    expect(pieces.join(" ")).not.toContain("framework and more");
    expect(pieces).not.toContain("matrix grid");
  });

  it("forbids the retired names and admits the product's own", () => {
    for (const bad of [
      "PPI Assessment Report",
      "A retake creates a new report",
      "the Tatva matrix",
      "our own framework",
      "the matching categories",
      "AI Score",
    ]) {
      expect(hits(bad), bad).not.toEqual([]);
    }
    for (const good of [
      "PRISM Report",
      "Tatva Assessment",
      "AI Match",
      "Save the skills on this job",
      "Proctoring Report",
      "SWOT",
      "JD",
    ]) {
      expect(hits(good), good).toEqual([]);
    }
  });
});

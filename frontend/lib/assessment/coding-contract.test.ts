// Nothing the browser receives about a coding question can carry its answer.
//
// The hidden tests, their expected outputs, the reference solution and the
// expected approach live in `coding_question_keys` on the server, which has no
// serializer (PLAN-p4 section 3.1, principle 3). The client types are the
// other half of that promise: if a field that could carry any of them were
// added here, a server change that started sending it would type-check and
// render. So the candidate view's field set is pinned EXACTLY, the way
// `test_miti_pipeline.py` pins an evaluator's input, rather than by the
// absence of a few names: a future field called `notes` must fail here too.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { describe, expect, it } from "vitest";

const HERE = fileURLToPath(new URL(".", import.meta.url));

/** The top-level field names of `export interface <name> { ... }` in a file. */
function interfaceFields(file: string, name: string): string[] {
  const source = readFileSync(join(HERE, file), "utf8");
  const start = source.indexOf(`export interface ${name} {`);
  if (start === -1) throw new Error(`${name} is not declared in ${file}`);
  let depth = 0;
  let body = "";
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    const char = source[i];
    if (char === "{") depth += 1;
    if (char === "}") depth -= 1;
    if (depth === 0) break;
    // Only what sits directly inside the interface, so a nested object type
    // contributes its own name and not its members.
    if (depth === 1 && char !== "{") body += char;
    if (depth > 1 && char === "{") body += "{}";
  }
  const withoutComments = body.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  return [...withoutComments.matchAll(/^\s*([a-z_]+)\??:/gm)].map((match) => match[1]);
}

/** The backend module that owns the candidate projection of a coding payload. */
const PAYLOAD_PY = join(HERE, "../../../backend/app/services/coding_assessment/payload.py");

/** `CANDIDATE_FIELDS` as the server declares it, in order. Read from the
 *  source rather than restated, so the two sides cannot drift apart. */
function serverCandidateFields(): string[] {
  const source = readFileSync(PAYLOAD_PY, "utf8");
  const match = source.match(/^CANDIDATE_FIELDS: tuple\[str, \.\.\.\] = \(([\s\S]*?)^\)/m);
  if (!match) throw new Error("CANDIDATE_FIELDS is not declared in payload.py");
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((field) => field[1]);
}

/** The declared fields of a pydantic class in payload.py, in order. */
function serverModelFields(name: string): string[] {
  const source = readFileSync(PAYLOAD_PY, "utf8");
  const start = source.indexOf(`class ${name}(`);
  if (start === -1) throw new Error(`${name} is not declared in payload.py`);
  const body = source.slice(source.indexOf("\n", start) + 1);
  const end = body.search(/^\S/m);
  const block = end === -1 ? body : body.slice(0, end);
  return [...block.matchAll(/^    ([a-z_]+): /gm)].map((field) => field[1]);
}

/** Names that would be a way for an answer to reach the browser. */
const ANSWER_SHAPED = /hidden|reference|solution|approach|answer|expected_output|rubric|key_/i;

describe("the candidate view of a coding question", () => {
  it("has exactly the fields the server's candidate projection returns", () => {
    // `services/coding_assessment/payload.CANDIDATE_FIELDS`, in order, read
    // from the server source; the literal list beside it is the reviewed
    // copy, so a change on either side fails here and names itself.
    expect(interfaceFields("contracts.ts", "CodingPayloadViewV2")).toEqual(serverCandidateFields());
    expect(serverCandidateFields()).toEqual([
      "payload_version",
      "title",
      "io",
      "input_format",
      "output_format",
      "constraints",
      "languages",
      "starter_code",
      "visible_tests",
      "limits",
    ]);
  });

  it("describes a sample test by its input and output and nothing else", () => {
    expect(interfaceFields("contracts.ts", "CodingVisibleTestView")).toEqual(
      serverModelFields("VisibleTest")
    );
    expect(serverModelFields("VisibleTest")).toEqual([
      "id",
      "stdin",
      "expected_stdout",
      "explanation",
    ]);
  });

  it("has no answer-shaped field anywhere a coding payload or a run result is typed", () => {
    const shapes: Array<[string, string]> = [
      ["contracts.ts", "CodingPayloadViewV2"],
      ["contracts.ts", "CodingVisibleTestView"],
      ["coding.ts", "CodingRunTestResult"],
      ["coding.ts", "CodingRunOut"],
    ];
    for (const [file, name] of shapes) {
      for (const field of interfaceFields(file, name)) {
        expect(field, `${name}.${field}`).not.toMatch(ANSWER_SHAPED);
      }
    }
  });

  it("returns no figure about a run: no timing, no memory and no score", () => {
    const fields = [
      ...interfaceFields("coding.ts", "CodingRunTestResult"),
      ...interfaceFields("coding.ts", "CodingRunOut"),
    ];
    for (const field of fields) {
      expect(field).not.toMatch(/time|memory|score|percent|rank|count/i);
    }
  });

  it("recognises the fields it pins, so an empty parse cannot pass", () => {
    expect(ANSWER_SHAPED.test("hidden_tests")).toBe(true);
    expect(ANSWER_SHAPED.test("reference_solution")).toBe(true);
    expect(ANSWER_SHAPED.test("expected_approach")).toBe(true);
    expect(interfaceFields("coding.ts", "CodingRunTestResult")).toEqual([
      "key",
      "passed",
      "result_word",
      "stdout",
      "expected_stdout",
      "stderr",
      "compile_output",
    ]);
  });
});

# PR #5 item B, the AI brief's lost zero (orchestrator hunk)

Source: PR #5 (`claude/stoic-knuth-9kt7t6`, commit 39965eeb), adapted to
`origin/main` at 5f104c0. Still missing on main, verified on 2026-09-28:
`frontend/app/(org)/org/jobs/new/page.tsx` builds the `POST /jobs/generate-jd`
body by hand with `Number(form.experience_min_years) || null`.

Not applied by package `s4-audit-close`: the Create Job page and
`lib/job-payload.ts` are job setup files, owned by the package implementing
the v10 job setup ruling. Apply it there or at integration.

## The defect

`0 || null` is `null` in JavaScript, and `Number("")` is `0`. A recruiter who
typed `0` in either experience box (legal: `ge=0`, and a fresher role starts
at zero years) sent `null`, and `JDGenerateIn` requires both ends as integers,
so the server answered 422 `experience_max_years: Input should be a valid
integer`. The page reported that 422 as "AI drafting is unavailable right
now", blaming an outage for a form the recruiter can fix, because nothing
validated the band before the request. `optionalNumber` already coerced
correctly and `buildJobCreatePayload` already used it; only the brief
hand-rolled its own coercion, so the two payloads disagreed about one band.

## 1. `frontend/lib/job-payload.ts`

```diff
-const optionalNumber = (value: string): number | null => {
+/**
+ * A numeric form field as the API wants it: the number, or null when the box
+ * is empty. Exported because the AI brief hand-rolled `Number(value) || null`,
+ * and `0 || null` is null: a recruiter who typed 0 (legal, `ge=0`) sent
+ * nothing and got a 422 under a toast blaming the AI. One coercion, used by
+ * both payloads, is what stops that coming back (PR #5).
+ */
+export const optionalNumber = (value: string): number | null => {
   const text = value.trim();
```

and append after `buildJobCreatePayload`:

```ts
/**
 * The Create-JD brief sent to `POST /jobs/generate-jd`. Lives beside
 * `buildJobCreatePayload` because the two carry the same experience band, and
 * when only one was built here the other drifted into a coercion that lost
 * zero. `brief` is the free-text Brief box; the API takes it as the
 * `key_requirements` alias it folds into `skills`.
 */
export function buildJdGeneratePayload(form: JobFormValues, brief: string) {
  return {
    title: form.title,
    department: form.department || null,
    grade: form.grade || null,
    skills: skillsToArray(form.skills),
    key_requirements: brief,
    reporting_to: form.reporting_to || null,
    experience_min_years: optionalNumber(form.experience_min_years),
    experience_max_years: optionalNumber(form.experience_max_years),
  };
}
```

## 2. `frontend/app/(org)/org/jobs/new/page.tsx`

```diff
-import { buildJobCreatePayload, type JobFormValues, skillsToArray } from "@/lib/job-payload";
+import {
+  buildJdGeneratePayload,
+  buildJobCreatePayload,
+  type JobFormValues,
+} from "@/lib/job-payload";
```

(drop `skillsToArray` only if nothing else in the page still uses it), and in
`generate`, after the title check and before `setGenerating(true)`:

```diff
+    // The band is REQUIRED by this endpoint, so check it here the way Publish
+    // does. Without it an incomplete or inverted band came back a 422 that the
+    // catch below announced as "AI drafting is unavailable right now".
+    if (!validateExperience()) {
+      document.getElementById("experience_min_years")?.scrollIntoView({ block: "center" });
+      return;
+    }
     setGenerating(true);
     try {
-      const res = await apiPost<unknown>("/jobs/generate-jd", {
-        title: form.title,
-        department: form.department || null,
-        grade: form.grade || null,
-        skills: skillsToArray(form.skills),
-        key_requirements: brief,
-        reporting_to: form.reporting_to || null,
-        experience_min_years: Number(form.experience_min_years) || null,
-        experience_max_years: Number(form.experience_max_years) || null,
-      });
+      const res = await apiPost<unknown>(
+        "/jobs/generate-jd",
+        buildJdGeneratePayload(form, brief),
+      );
```

Confirm the experience input's element id is `experience_min_years` in the
page as it stands after the v10 work; scroll to whatever id it carries.

## 3. `frontend/lib/job-payload.test.ts` (import both names, then append)

```ts
describe("optionalNumber", () => {
  it("keeps a zero instead of turning it into null", () => {
    expect(optionalNumber("0")).toBe(0);
  });
  it("returns null only for a genuinely empty box", () => {
    expect(optionalNumber("")).toBeNull();
    expect(optionalNumber("   ")).toBeNull();
  });
  it("returns null rather than NaN for text that is not a number", () => {
    expect(optionalNumber("four")).toBeNull();
  });
});

describe("buildJdGeneratePayload", () => {
  it("sends a zero-year minimum and maximum as 0, not null", () => {
    const payload = buildJdGeneratePayload(
      { ...completeForm, experience_min_years: "0", experience_max_years: "0" },
      "a short brief",
    );
    expect(payload.experience_min_years).toBe(0);
    expect(payload.experience_max_years).toBe(0);
  });
  it("agrees with the create payload about the band", () => {
    const form = { ...completeForm, experience_min_years: "0", experience_max_years: "5" };
    const generate = buildJdGeneratePayload(form, "");
    const create = buildJobCreatePayload(form);
    expect(generate.experience_min_years).toBe(create.experience_min_years);
    expect(generate.experience_max_years).toBe(create.experience_max_years);
  });
});
```

Mutation check to run after applying: put `Number(value) || null` back inside
`buildJdGeneratePayload`; the zero tests fail with `expected null to be +0`.

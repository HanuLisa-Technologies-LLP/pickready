## Current hard rules, the coding editor is Monaco and it runs code (2026-09-25)

Draft for the orchestrator to assemble into CLAUDE.md. Phase 4, WP-4D
(frontend only). No migration, no backend file.

### MONACO IS SELF-HOSTED, AND THE CSP DID NOT MOVE

`@monaco-editor/react` defaults to loading the editor from a public CDN, which
the Content-Security-Policy in `next.config.js` refuses. The AMD build is
copied from `node_modules/monaco-editor/min/vs` into `public/monaco/vs` by
`scripts/copy-monaco.mjs` on `predev` and `prebuild` (the
`build-proctoring-workers.mjs` precedent: Turbopack does not bundle it, the
copy is gitignored, the Docker builder runs `npm run build`), and
`lib/assessment/monaco-setup.configureMonacoLoader` points the loader at
`/monaco/vs` before any editor mounts.

- **Both packages are pinned EXACT** (`monaco-editor` 0.56.0,
  `@monaco-editor/react` 4.7.0). The served copy is whatever version resolved,
  so a caret would let the editor a candidate uses change under a lockfile
  refresh with no diff to read. `monaco-self-hosted.test.ts` pins the pins,
  the copy script's output path against the loader path, the `.gitignore` and
  lint exclusions, and a CSP that admits the editor and names no CDN.
- **The copy script REFUSES a build whose package no longer ships
  `loader.js`, `editor/editor.main.js` and `editor/editor.main.css`**, so a
  changed AMD layout fails the image build rather than a candidate.
- **Verified in a real browser under the product's exact CSP** (minus
  `upgrade-insecure-requests`, which only matters over https): the loader, the
  python grammar, the codicon font and a web worker all loaded with ZERO
  `securitypolicyviolation` events. `worker-src 'self' blob:` and
  `font-src data:` were already there.
- **`proxy.ts` is unchanged, and that is a decision, not an oversight.** Its
  extension clause excludes image types only, so `/monaco/vs/*.js` passes the
  deny-by-default branch like a page. The editor mounts only on the
  candidate's assessment page and the recruiter's transcript, both signed in,
  and every file the loader fetches carries the session cookie. A coding
  editor on a signed-out page would 307 its own scripts to /login; the test
  pins that answer so the day it matters is a test failure.

### CODEMIRROR IS GONE, ALL OF IT

`codemirror`, the ten `@codemirror/*` packages, `@lezer/highlight`,
`lib/assessment/coding-languages.ts` and `lib/assessment/jsdom-shims.ts` are
deleted. One editor implementation (rule 5). The editor keeps its props
contract (`value, language, onChange, readOnly, disabled, fieldHooks,
onSubmitShortcut, ariaLabel`), so the transcript and the player did not
change shape.

### COPY, CUT, PASTE AND DROP ARE REFUSED IN TWO LAYERS, AND EACH IS NAMED

The old editor refused paste and drop only; copy and cut passed, which the
audit recorded as a confirmed gap against Appendix B section 3.

- **Layer one is DOM listeners on the editor's host, in the CAPTURE phase**
  (`lib/assessment/editor-guard.ts`): the clipboard events, `beforeinput`
  with a paste, drop, yank, cut or drag input type (Monaco types through a
  hidden textarea, so a paste can arrive as input rather than as a clipboard
  event), and the clipboard CHORDS at keydown (Ctrl/Cmd+C, X, V,
  Ctrl+Shift+V, Ctrl+Insert, Shift+Insert, Shift+Delete), read from the
  physical key as well as the character so a Cyrillic layout's Ctrl+V is
  refused. AltGr (Ctrl+Alt) is never swallowed.
- **Layer two is `editor.addCommand` over the same chords**, for any path by
  which a keystroke reaches the editor without passing the host. F1 (the
  command palette, which runs Paste through the Clipboard API) is swallowed
  without a report.
- **Every attempt is reported ONCE, WITH ITS KIND**, through
  `fieldHooks.onBlockedAction(kind)`. A chord refused at keydown never
  produces the clipboard event behind it, so the two layers cannot both
  report one attempt. A cut is reported as a cut, not as a paste.
- **The keystroke recorder is on the host in the capture phase**, registered
  BEFORE the guard, so a refused Ctrl+V is still a keystroke in the answer's
  record and no library upgrade that reorders Monaco's own dispatch can stop
  the recording.
- **Every suggestion surface is off**: completion, snippets, parameter hints,
  inline suggestions, hovers, code lenses, light bulbs, inlay hints, links,
  validation decorations, auto-closing, and the JavaScript and TypeScript
  language services. An editor that completes code is a hint engine.
- The read-only transcript view installs neither layer: a recruiter reading
  submitted code is not being assessed.

### RUN IS NOT SUBMIT, AND CTRL+ENTER RUNS

`components/assessment/coding-answer.tsx` renders the v2 payload
(`CodingPayloadViewV2`, exactly `payload.CANDIDATE_FIELDS`): the title, the
input and output formats, the constraints, each visible sample named by its
POSITION ("Sample one"), the language selector over `payload.languages`, and
Run.

- **One draft per language.** Switching keeps what was written in the
  language being left; `starterCodeFor` compares an answer with the starter
  of the language it is IN.
- **Run executes the visible tests only** (`POST .../coding/{qid}/runs`, then
  a poll of `GET .../runs/{run_id}` backing off 500 ms to 2 s, giving up with
  a sentence after two minutes). The panel shows each sample's result word,
  the candidate's output beside the expected output, stderr, and the compiler
  output once when every test shares it. No timing, no memory figure, no
  score, and counts are written out.
- **A refusal is the SERVER's sentence verbatim** (409 in progress, 429 the
  cap, 503 the runner); with no sentence, the panel's own words, never "API
  error 503", which is a number on an assessment screen.
- **The client token is minted once per click and REUSED on a retry of a
  request that never arrived**; a press after the server has answered is a
  new run, even of identical code. The question is part of the match because
  the server keys a token on the conversation.
- **Ctrl/Cmd+Enter inside the editor RUNS; it never sends.** A coding answer
  is final, so the keys a candidate types with must not be one chord from an
  irreversible act. The final answer is `CodingSubmitButton`, a
  `ConfirmButton` that focuses "Keep working", names the language being
  submitted and says there is no way back.
- **A version 1 question in the player is SAID, not rendered** as an editor
  whose Run could only fail. No conversation issues one (pilot holds zero
  assessment conversations); the transcript still shows stored v1 answers
  with their not-executed note.

### THE CONTRACT IS PINNED FROM THE CLIENT SIDE TOO

`coding-contract.test.ts` pins the candidate view's field set EXACTLY (so a
future `notes` field fails too) and sweeps every coding payload and run
result type for an answer-shaped name and for any timing, memory or score
field. `coding-languages-parity.test.ts` reads
`backend/app/services/code_execution/languages.py` and requires the labels
and the run hints to be the backend's words, in both directions, because the
Java hint is a rule the sandbox enforces. `monaco-theme.test.ts` builds the
theme from both CSS blocks and asserts `inherit: false` (the stock theme's
control keywords are purple), no purple hue anywhere, ink for text, comments
and line numbers, navy keywords and teal literals.

### SUPERSESSIONS

- **2026-09-02 "coding is judged by READING and every evaluation and every
  recruiter view says the code was not executed"**: superseded for v2
  questions by Phase 4. The transcript shows what the hidden tests showed,
  the compiler output and the code-quality review; `not_executed_note` is
  rendered only when a stored legacy row carries it.
- **Ctrl/Cmd+Enter sending the answer from the code editor** (the CodeMirror
  editor's behaviour, pinned by the old `question-renderer.test.tsx`): in the
  coding format it now runs the samples. Text fields still send on it.

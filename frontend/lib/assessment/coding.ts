// The coding question's client: the Run button's two routes, the languages a
// candidate is told about, and the conversation the question belongs to.
//
// WHAT RUN IS, AND WHAT IT IS NOT. Run executes the candidate's code against
// the question's VISIBLE sample tests only, as often as the server's limits
// allow, and shows each test's result, output and errors. It records nothing
// that is graded. The final answer goes through the conversation's one Send
// control (`coding-submit-button.tsx` confirms it), and only then does the
// server run the hidden tests, which never reach this browser in any form.
//
// THE ROUTES (candidate audience, the /api/v2/assessments mount):
//   POST .../conversations/{cid}/coding/{qid}/runs          -> 202 CodingRunStarted
//   GET  .../conversations/{cid}/coding/{qid}/runs/{run_id} -> CodingRunOut
// A refusal (409 another run in flight or the question is not open, 429 the
// rate or per-question cap, 503 the runner is unavailable) carries a sentence
// the server wrote, and the panel shows it verbatim, because a sentence
// written in two places drifts.
//
// THE RESPONSE SHAPES ARE THIS FILE'S TYPES. PLAN-p4 section 3.7 names the
// fields; the one change is that a result carries the visible test's `key`
// (the payload's `visible_tests[].id`) rather than a display name, so the
// panel labels a result exactly as the question labels its sample, by
// position, and the server writes no second name that could disagree.
//
// IDEMPOTENT ON A TOKEN THE CLIENT MINTS. One click is one token. A POST whose
// response never arrived is retried with the SAME token, so the server
// returns the run it already started instead of starting a second one against
// the candidate's limit; a change to the code or the language mints a new
// token, because the old one names a run of different code.

import * as React from "react";

import { api, ApiError, NETWORK_ERROR } from "@/lib/api";

/** The mount every assessment route lives on (`backend/app/main.py`). */
export const ASSESSMENT_CONVERSATIONS = "/api/v2/assessments/conversations";

/** `models/coding.RUN_STATUSES`. `unavailable` is the runner's outage and
 *  `failed` a defect on the server's side; neither is the candidate's code. */
export type CodingRunStatus = "queued" | "complete" | "unavailable" | "failed";

/** `POST .../runs`, 202. */
export interface CodingRunStarted {
  run_id: string;
  status: CodingRunStatus;
}

/** One visible sample test's result. Words and the candidate's own output
 *  only: no timing, no memory figure, no score. */
export interface CodingRunTestResult {
  /** The visible test this result is for: `visible_tests[].id`. */
  key: string;
  passed: boolean;
  /** Passed, Wrong answer, Compilation error, Time limit exceeded, ... */
  result_word: string;
  stdout: string;
  expected_stdout: string;
  stderr: string;
  compile_output: string;
}

/** `GET .../runs/{run_id}`. */
export interface CodingRunOut {
  run_id: string;
  status: CodingRunStatus;
  /** Empty until the run completes; one entry per visible test, in order. */
  tests: CodingRunTestResult[];
  /** The server's sentence when the run could not be completed. */
  message: string | null;
}

export interface CodingRunRequest {
  language: string;
  source: string;
  client_token: string;
}

export function runsPath(conversationId: string, questionId: string): string {
  return `${ASSESSMENT_CONVERSATIONS}/${encodeURIComponent(conversationId)}/coding/${encodeURIComponent(questionId)}/runs`;
}

export function startRun(
  conversationId: string,
  questionId: string,
  body: CodingRunRequest,
  signal?: AbortSignal
): Promise<CodingRunStarted> {
  return api<CodingRunStarted>(runsPath(conversationId, questionId), {
    method: "POST",
    body,
    signal,
  });
}

export function fetchRun(
  conversationId: string,
  questionId: string,
  runId: string,
  signal?: AbortSignal
): Promise<CodingRunOut> {
  return api<CodingRunOut>(
    `${runsPath(conversationId, questionId)}/${encodeURIComponent(runId)}`,
    { method: "GET", signal }
  );
}

// ---------------------------------------------------------------------------
// The client token
// ---------------------------------------------------------------------------

/** A click that has not had a definite answer from the server yet. */
export interface PendingRun {
  token: string;
  questionId: string;
  language: string;
  source: string;
}

/**
 * The token for a Run click: the pending one when it names exactly this code
 * in exactly this language for exactly this question (a retry of the same
 * click), otherwise a new one.
 *
 * The question is part of the match because the server keys a token on the
 * CONVERSATION: the same starter code on the next question, reusing the
 * token, would be answered with the earlier question's run.
 */
export function tokenForRun(
  pending: PendingRun | null,
  attempt: Omit<PendingRun, "token">,
  mint: () => string = mintClientToken
): PendingRun {
  if (
    pending &&
    pending.questionId === attempt.questionId &&
    pending.language === attempt.language &&
    pending.source === attempt.source
  ) {
    return pending;
  }
  return { token: mint(), ...attempt };
}

export function mintClientToken(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

/** First wait after the POST, the growth factor, and the ceiling: quick
 *  enough that a fast run feels immediate, slow enough that a queued one
 *  does not hammer a server whose every poll performs a bounded fetch from
 *  the runner. */
export const POLL_INITIAL_MS = 500;
export const POLL_FACTOR = 1.5;
export const POLL_MAX_MS = 2000;
/** How long the panel waits for one run before it stops and says so. */
export const POLL_GIVE_UP_MS = 120_000;
/** Consecutive polls that may fail to reach the server at all before the
 *  panel stops. A single blip is retried; a dead connection is reported. */
export const POLL_NETWORK_FAILURES = 5;

/** The run did not finish within `POLL_GIVE_UP_MS`. */
export class RunStillRunning extends Error {
  constructor() {
    super(
      "Your run is taking longer than expected, so this page has stopped waiting for it. " +
        "Your code is kept; you can run it again."
    );
    this.name = "RunStillRunning";
  }
}

export function nextPollDelay(previous: number): number {
  return Math.min(POLL_MAX_MS, Math.round(previous * POLL_FACTOR));
}

function abortError(): DOMException {
  return new DOMException("The run poll was stopped.", "AbortError");
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface PollOptions {
  conversationId: string;
  questionId: string;
  runId: string;
  signal?: AbortSignal;
  /** Injected by tests. */
  fetchOnce?: typeof fetchRun;
  wait?: (ms: number, signal?: AbortSignal) => Promise<void>;
  now?: () => number;
}

/**
 * Poll one run until it is no longer queued.
 *
 * Resolves with the final state (`complete` or `unavailable`). Rejects with
 * the server's `ApiError` when it refuses a poll (the sentence is in
 * `message`), with `RunStillRunning` after `POLL_GIVE_UP_MS`, with the last
 * network `ApiError` after `POLL_NETWORK_FAILURES` consecutive transport
 * failures, and with an `AbortError` when `signal` aborts (the question was
 * left or the component unmounted), which the caller ignores.
 */
export async function pollRun({
  conversationId,
  questionId,
  runId,
  signal,
  fetchOnce = fetchRun,
  wait = sleep,
  now = Date.now,
}: PollOptions): Promise<CodingRunOut> {
  const started = now();
  let delay = POLL_INITIAL_MS;
  let networkFailures = 0;
  for (;;) {
    await wait(delay, signal);
    let run: CodingRunOut | null = null;
    try {
      run = await fetchOnce(conversationId, questionId, runId, signal);
      networkFailures = 0;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== NETWORK_ERROR) throw error;
      networkFailures += 1;
      if (networkFailures >= POLL_NETWORK_FAILURES) throw error;
    }
    if (run && run.status !== "queued") return run;
    if (now() - started >= POLL_GIVE_UP_MS) throw new RunStillRunning();
    delay = nextPollDelay(delay);
  }
}

// ---------------------------------------------------------------------------
// The conversation a coding question belongs to
// ---------------------------------------------------------------------------

/**
 * The id of the assessment conversation on screen. The Run routes are scoped
 * to it, and the answer components' props contract (`contracts.ts`) carries
 * the question but not the conversation, so the player provides it here.
 */
export const CodingConversationContext = React.createContext<string | null>(null);

/**
 * The conversation id, or a thrown error naming the missing provider. A
 * coding question outside a conversation is a wiring bug, and an editor whose
 * Run button quietly did nothing would hide it until a candidate found it.
 */
export function useCodingConversationId(): string {
  const conversationId = React.useContext(CodingConversationContext);
  if (!conversationId) {
    throw new Error(
      "A coding question was rendered outside an assessment conversation. The player " +
        "must wrap the question in CodingConversationContext.Provider with the conversation id."
    );
  }
  return conversationId;
}

// ---------------------------------------------------------------------------
// What a candidate is told about each language
// ---------------------------------------------------------------------------

/**
 * How the sandbox runs each language, in the words the backend's language
 * registry uses (`backend/app/services/code_execution/languages.py`,
 * `LanguageSpec.source_hint`). `coding-languages-parity.test.ts` reads that
 * file and fails when the two disagree, because the Java sentence in
 * particular is a rule the sandbox enforces: code in any class other than
 * Main does not compile there.
 */
export const CODING_LANGUAGE_HINTS: Record<string, string> = {
  python: "Read input from standard input and print the answer to standard output.",
  java:
    "Declare a public class named Main with a main method. Read input " +
    "from standard input and print the answer to standard output.",
  cpp: "Write a main function. Read input from standard input and print the answer to standard output.",
  javascript: "Read input from standard input and print the answer to standard output.",
};

export function languageHint(language: string): string | null {
  return CODING_LANGUAGE_HINTS[language] ?? null;
}

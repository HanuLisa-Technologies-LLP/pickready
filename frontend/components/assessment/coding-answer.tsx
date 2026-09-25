"use client";

// The coding question (assessment spec 5): the problem's input and output
// formats, its constraints and its visible sample tests, the Monaco editor,
// and a Run button that executes the code against those sample tests only.
//
// ONE DRAFT PER LANGUAGE. The selector lists the languages this deployment
// offers (the server sends them on the question; they come from
// `CODE_EXECUTION_LANGUAGES`), and switching keeps what was written in the
// language being left, so a candidate who looks at the Java starter never
// loses their Python. Drafts live for as long as the question is on screen;
// the player's autosave keeps the one being answered.
//
// RUN IS NOT SUBMIT. Run shows how the code does on the samples and records
// nothing that is graded; Ctrl/Cmd+Enter inside the editor runs too, so the
// keys a candidate types with can never send an irreversible answer. The final
// answer goes through the player's Send control, which for a coding question
// is `CodingSubmitButton` and asks for confirmation.
//
// VERSION 2 ONLY. A question issued before code execution (payload version 1)
// has no sample tests and nothing to run against, and no conversation issues
// one any more. Should one reach the player it is SAID, not rendered as an
// editor whose Run button could only fail.

import * as React from "react";
import { Loader2, Play } from "lucide-react";

import { CodeEditor } from "@/components/assessment/code-editor";
import {
  CodingRunResults,
  sampleLabel,
  type RunPanelState,
} from "@/components/assessment/coding-run-results";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, NETWORK_ERROR } from "@/lib/api";
import { isCodingAnswer, languageLabel } from "@/lib/assessment/answers";
import {
  languageHint,
  pollRun,
  RunStillRunning,
  startRun,
  tokenForRun,
  useCodingConversationId,
  type PendingRun,
} from "@/lib/assessment/coding";
import {
  isCodingPayloadV2,
  type AnswerComponentProps,
  type CodingPayloadViewV2,
} from "@/lib/assessment/contracts";

const IDLE: RunPanelState = { kind: "idle" };

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

const RUN_NOT_STARTED =
  "The run could not be completed. Your code is kept; you can run it again.";

/**
 * The sentence the panel shows when a run stops without results.
 *
 * The server's own sentence when it wrote one (a 409, 429 or 503 carries it
 * in `detail`), verbatim. Otherwise this module's words: the client's generic
 * "API error 503" names a status code, which is a number on an assessment
 * screen and tells a candidate nothing they can act on.
 */
export function stoppedMessage(error: unknown): string {
  if (error instanceof RunStillRunning) return error.message;
  if (error instanceof ApiError) {
    if (error.status === NETWORK_ERROR) return error.message;
    const detail = (error.detail as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && detail.trim().length > 0) return detail;
  }
  return RUN_NOT_STARTED;
}

export function CodingAnswer(props: AnswerComponentProps) {
  if (!isCodingPayloadV2(props.question.payload)) {
    return (
      <p
        className="border border-border bg-muted p-3 text-sm"
        role="alert"
        data-testid="coding-unsupported"
      >
        This coding question was issued in an older format that cannot be run here. Please
        contact the recruiter who invited you.
      </p>
    );
  }
  return <ExecutableCodingAnswer {...props} payload={props.question.payload} />;
}

function starterOf(payload: CodingPayloadViewV2, language: string): string {
  return payload.starter_code[language] ?? "";
}

function ExecutableCodingAnswer({
  question,
  prompt,
  value,
  onChange,
  disabled,
  fieldHooks,
  payload,
}: AnswerComponentProps & { payload: CodingPayloadViewV2 }) {
  const conversationId = useCodingConversationId();
  const firstLanguage = payload.languages[0] ?? "";
  const current = isCodingAnswer(value)
    ? value
    : { language: firstLanguage, code: starterOf(payload, firstLanguage) };

  // Drafts of the languages NOT on screen. Emptied when the question
  // changes: the player keeps one answer component mounted across
  // consecutive coding questions, and a draft belongs to one question.
  const drafts = React.useRef(new Map<string, string>());
  const pending = React.useRef<PendingRun | null>(null);
  const inFlight = React.useRef<AbortController | null>(null);

  // The panel is recorded WITH the question it describes, so a run that
  // finishes after the candidate has moved on can never paint its results
  // under the next question.
  const [panelFor, setPanelFor] = React.useState<{
    questionId: string;
    state: RunPanelState;
  }>({ questionId: question.id, state: IDLE });
  const panel = panelFor.questionId === question.id ? panelFor.state : IDLE;
  const running = panel.kind === "running";

  // Leaving the question (or the page) stops the poll; the run itself is the
  // server's and finishes there regardless.
  React.useEffect(() => {
    drafts.current = new Map();
    pending.current = null;
    return () => {
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [question.id]);

  const switchLanguage = (language: string) => {
    if (language === current.language) return;
    drafts.current.set(current.language, current.code);
    onChange({
      language,
      code: drafts.current.get(language) ?? starterOf(payload, language),
    });
  };

  const run = async () => {
    if (running || disabled || current.code.trim().length === 0) return;
    const questionId = question.id;
    const attempt = tokenForRun(pending.current, {
      questionId,
      language: current.language,
      source: current.code,
    });
    pending.current = attempt;
    const controller = new AbortController();
    inFlight.current?.abort();
    inFlight.current = controller;
    const show = (state: RunPanelState) => setPanelFor({ questionId, state });
    show({ kind: "running" });
    try {
      const started = await startRun(
        conversationId,
        questionId,
        {
          language: attempt.language,
          source: attempt.source,
          client_token: attempt.token,
        },
        controller.signal
      );
      // The server answered, so this click is settled: a later press is a
      // new run, even of identical code.
      pending.current = null;
      const finished = await pollRun({
        conversationId,
        questionId,
        runId: started.run_id,
        signal: controller.signal,
      });
      show({ kind: "complete", run: finished });
    } catch (error) {
      if (isAbort(error)) return;
      // A POST that never reached the server keeps its token, so pressing
      // Run again retries THIS click rather than starting a second run.
      if (!(error instanceof ApiError && error.status === NETWORK_ERROR)) {
        pending.current = null;
      }
      show({ kind: "stopped", message: stoppedMessage(error) });
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  };

  const selectorId = `language-${question.id}`;
  const hint = languageHint(current.language);
  // Nothing to run is not a run: the server would refuse an empty source,
  // and a press that can only fail is a control that should not be offered.
  const runnable = current.code.trim().length > 0;

  return (
    <div className="space-y-4">
      <ProblemDetails payload={payload} />

      <div className="space-y-2">
        {payload.languages.length > 1 ? (
          <div className="flex items-center gap-2">
            <Label htmlFor={selectorId} className="text-xs">
              Language
            </Label>
            <Select
              value={current.language}
              disabled={disabled || running}
              onValueChange={switchLanguage}
            >
              <SelectTrigger id={selectorId} className="h-9 w-52">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {payload.languages.map((language) => (
                  <SelectItem key={language} value={language}>
                    {languageLabel(language)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <p className="text-xs font-medium" data-testid="language-indicator">
            {languageLabel(current.language)}
          </p>
        )}
        {hint ? (
          <p className="text-xs" data-testid="language-hint">
            {hint}
          </p>
        ) : null}
      </div>

      <CodeEditor
        value={current.code}
        language={current.language}
        onChange={(code) => onChange({ ...current, code })}
        disabled={disabled}
        fieldHooks={fieldHooks}
        onSubmitShortcut={() => void run()}
        ariaLabel={`Your code for: ${prompt}`}
        height="clamp(18rem, 55vh, 40rem)"
      />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="outline"
          onClick={() => void run()}
          disabled={disabled || running || !runnable}
          data-testid="coding-run"
        >
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Play className="h-4 w-4" aria-hidden="true" />
          )}
          {running ? "Running" : "Run sample tests"}
        </Button>
        <p className="text-xs">
          Running checks your code against the sample tests only and is not your final
          answer. Ctrl+Enter (Cmd+Enter on Mac) in the editor runs it too.
        </p>
      </div>

      <CodingRunResults state={panel} samples={payload.visible_tests} />
    </div>
  );
}

function ProblemDetails({ payload }: { payload: CodingPayloadViewV2 }) {
  const sections: Array<[string, string]> = [
    ["Input format", payload.input_format],
    ["Output format", payload.output_format],
    ["Constraints", payload.constraints],
  ];
  return (
    <div className="space-y-3">
      {payload.title.trim().length > 0 ? (
        <h3 className="text-base font-semibold" data-testid="coding-title">
          {payload.title}
        </h3>
      ) : null}
      {sections
        .filter(([, text]) => text.trim().length > 0)
        .map(([label, text]) => (
          <div key={label} className="border border-border bg-muted p-3 text-sm">
            <p className="text-xs font-semibold uppercase tracking-wide">{label}</p>
            <p className="mt-1 whitespace-pre-wrap">{text}</p>
          </div>
        ))}
      {payload.visible_tests.length > 0 ? (
        <div className="space-y-2" data-testid="coding-samples">
          <p className="text-xs font-semibold uppercase tracking-wide">Sample tests</p>
          {payload.visible_tests.map((test, index) => (
            <div
              key={test.id}
              className="grid gap-2 border border-border p-3 sm:grid-cols-2"
            >
              <p className="text-sm font-medium sm:col-span-2">{sampleLabel(index)}</p>
              <SampleBlock label="Input" text={test.stdin} />
              <SampleBlock label="Expected output" text={test.expected_stdout} />
              {test.explanation.trim().length > 0 ? (
                <p className="text-sm sm:col-span-2">{test.explanation}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SampleBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-semibold uppercase tracking-wide">{label}</p>
      {text.length > 0 ? (
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words bg-muted p-2 font-mono text-xs">
          {text}
        </pre>
      ) : (
        <p className="mt-1 text-xs italic">Empty.</p>
      )}
    </div>
  );
}

/**
 * The shared contracts between the assessment player, the question-format
 * components and the proctoring client.
 *
 * ONE dispatcher, ONE props contract, one implementation per format
 * (assessment spec 5.1), and ONE bridge through which every answer component
 * reaches proctoring (proctoring spec 4.5). These types are the boundary; the
 * two features are built by different modules against them and neither
 * imports the other's internals.
 *
 * Every shape here mirrors a pydantic model on the backend (the single-mode
 * assessment, 2026-09-24, PLAN-p3 sections 3.4 to 3.6):
 *   QuestionOut          schemas QuestionOut
 *   AnswerPayload        services/assessment_formats/types.ANSWER_MODELS
 *   AnswerBehaviour      schemas AnswerBehaviourIn
 *   ConversationTurn     schemas ConversationOut
 *   RespondBody          schemas ConversationMessageIn (extra="forbid")
 *   DraftBody            the PUT /conversations/{id}/draft body
 *   VoiceAnswerOut       the /conversations/{id}/voice/* responses
 *
 * THE SERVER OWNS THE CLOCK. A turn arrives with its deadline and the
 * server's own "now"; the countdown on screen is a rendering of those two
 * instants and nothing the client measures is ever sent back as time. The
 * client-reported paused duration this contract used to carry was exactly
 * that, a number the client chose, and it is gone: the server keeps its own
 * pause intervals and refuses the old field with a 422.
 */

export type QuestionType =
  | "evidence_based"
  | "mcq_single"
  | "mcq_multi"
  | "fill_blank"
  | "coding"
  | "short_answer";

/** The two prose formats. Everything else is a structure. */
export const TEXT_TYPES: readonly QuestionType[] = ["evidence_based", "short_answer"];

export function isTextType(type: QuestionType): boolean {
  return TEXT_TYPES.includes(type);
}

export interface McqOptionView {
  id: string;
  text: string;
}

/** The candidate view of an MCQ payload: options in THIS candidate's order,
 *  no correct id anywhere. `select_count` is 1 for a single-answer question,
 *  a number when the candidate is told how many to pick, null for "select all
 *  that apply". */
export interface McqPayloadView {
  options: McqOptionView[];
  select_count: number | null;
}

export interface FillBlankView {
  index: number;
  case_sensitive: boolean;
  /** Sizes the inline input to the expected answer without revealing it. */
  expected_length: number;
}

export interface FillBlankPayloadView {
  /** Each `___` is one blank, in order. */
  template: string;
  blanks: FillBlankView[];
}

/** A coding question issued before payload version 2: one language and one
 *  starter. Old rows only; nothing new is issued in this shape. */
export interface CodingPayloadView {
  language: string;
  language_options: string[];
  starter_code: string;
  constraints: string;
}

/** One visible sample test: what the candidate may run their code against
 *  and read. `id` is the server's opaque key (`v1`, `v2`, ...) and is never
 *  shown; a sample is named by its position. The hidden tests the final
 *  answer is checked against are not a field of anything the browser
 *  receives. */
export interface CodingVisibleTestView {
  id: string;
  stdin: string;
  expected_stdout: string;
  /** Why the sample's output is what it is; may be empty. */
  explanation: string;
}

/** The limits one language's programs run under, as the server stores them.
 *  Carried on the payload because the server sends it; NOTHING renders it,
 *  because every figure here is a number on an assessment screen. */
export interface CodingLanguageLimitsView {
  cpu_seconds: number;
  cpu_extra_seconds: number;
  wall_seconds: number;
  memory_kb: number;
  stack_kb: number;
  max_processes: number;
  max_file_kb: number;
  max_output_chars: number;
}

/**
 * The candidate view of a coding question at payload version 2: exactly the
 * keys `services/coding_assessment/payload.candidate_projection` returns
 * (`CANDIDATE_FIELDS`).
 *
 * Candidate-safe BY CONSTRUCTION: the hidden tests, their expected outputs
 * and the reference solution live in `coding_question_keys` on the server and
 * are never part of a payload, so this type has no field that could carry
 * them, and `coding-contract.test.ts` fails if one is ever added.
 */
export interface CodingPayloadViewV2 {
  payload_version: 2;
  title: string;
  io: "stdin_stdout";
  input_format: string;
  output_format: string;
  constraints: string;
  /** The languages this question offers, in configured order. */
  languages: string[];
  /** Starter code keyed by language, one entry per offered language. */
  starter_code: Record<string, string>;
  visible_tests: CodingVisibleTestView[];
  limits: Record<string, CodingLanguageLimitsView>;
}

export function isCodingPayloadV2(
  payload: QuestionPayloadView
): payload is CodingPayloadViewV2 {
  return (payload as { payload_version?: unknown }).payload_version === 2;
}

export type QuestionPayloadView =
  | McqPayloadView
  | FillBlankPayloadView
  | CodingPayloadView
  | CodingPayloadViewV2
  | Record<string, never>;

export interface QuestionOut {
  id: string;
  question_type: QuestionType;
  payload: QuestionPayloadView;
  time_allocation_seconds: number;
}

/** The answer, in the shape the server validates for the question's type. */
export type AnswerPayload =
  | { text: string }
  | { selected_option_id: string }
  | { selected_option_ids: string[] }
  | { values: string[] }
  | { language: string; code: string };

/** What the server measures a turn from. Timings only, never characters. */
export interface AnswerBehaviour {
  keydown_offsets_ms: number[];
  backspace_offsets_ms: number[];
  blocked_action_count: number;
  focus_ms: number;
  mouse_samples: number;
  mouse_path_px: number;
  mouse_idle_ms: number;
  mouse_clicks: number;
  option_click_offsets_ms: number[];
  scroll_events: number;
}

/** Why the server is holding the current turn's clock still. */
export type PauseReason = "device_loss" | "transcription" | "warning";

/**
 * The current turn's clock, as the server computed it at `server_now`.
 *
 * `deadline_at` already includes every pause the server has recorded for this
 * turn, so `deadline_at - server_now` is the time the candidate has left at
 * the instant the response was written. While `paused` is true that
 * difference does not shrink, because the server extends the deadline for as
 * long as the pause lasts.
 */
export interface TurnClock {
  /** The server's name for the kind of turn. Rendered by nothing and
   *  branched on by nothing here: the format comes from `question`. */
  kind: string;
  allocation_seconds: number;
  deadline_at: string;
  server_now: string;
  paused: boolean;
  pause_reason: PauseReason | null;
}

/**
 * One exchange already answered, read-only (fixed order: past answers are
 * viewable and never editable). Mirrors `HistoryEntryOut`.
 *
 * Both lines are the server's, verbatim: `question` as the candidate read it,
 * `answer` as the server recorded it (prose as submitted, a spoken answer's
 * final transcript, a structured answer's rendering, or the server's own
 * sentence for a turn that ran out of time with nothing given). The client
 * authors none of it.
 */
export interface HistoryEntry {
  question: string;
  answer: string;
}

/**
 * Where the conversation stands.
 *
 * `preparing`: the questions for this candidate are being written; ask again
 * shortly. `paused`: the proctoring layer paused the session (a camera or
 * microphone was lost) and no answer is accepted until it resumes.
 */
export type ConversationStatus =
  | "active"
  | "preparing"
  | "paused"
  | "completed"
  | "terminated";

export interface ConversationTurn {
  conversation_id: string;
  status: ConversationStatus;
  prompt: string | null;
  progress_label: string;
  answered_questions: number;
  total_questions: number;
  is_reask: boolean;
  /** The identity of the turn on screen. Every submission names it, and a
   *  submission naming a turn that is no longer current is refused with a
   *  409 and writes nothing, so a retry after a lost response can never be
   *  filed as the answer to the next question. */
  turn_seq: number;
  /** Null when no turn is open (preparing, completed, terminated). */
  turn: TurnClock | null;
  /** Whether spoken answers can be taken right now (the server's
   *  transcription is configured). False means the microphone control is
   *  not rendered at all, never rendered and refused. */
  voice_input_available: boolean;
  /** Every exchange already answered, oldest first. */
  history: HistoryEntry[];
  answer_message_id?: string | null;
  question?: QuestionOut | null;
  termination_message?: string | null;
}

/** The body of POST /conversations/{id}/respond. The server forbids any
 *  other key, so a stale client that still sends a time it measured itself
 *  is refused with a 422 rather than believed. */
export interface RespondBody {
  turn_seq: number;
  answer: string;
  answer_payload?: AnswerPayload;
  /** A transcribed spoken answer. When present the server evaluates the
   *  transcript it holds and ignores `answer`. */
  voice_answer_id?: string;
  /** True when the turn's clock submitted the answer rather than the
   *  candidate. An empty timed-out answer is recorded as an evidence gap. */
  timed_out?: boolean;
  behaviour?: AnswerBehaviour;
}

/** The body of PUT /conversations/{id}/draft. What the server submits on
 *  the candidate's behalf if the turn expires before anything else arrives. */
export interface DraftBody {
  turn_seq: number;
  answer?: string;
  answer_payload?: AnswerPayload;
}

export type VoiceAnswerStatus =
  | "recording"
  | "uploaded"
  | "transcribing"
  | "transcribed"
  | "failed"
  | "consumed";

/** The /conversations/{id}/voice/* responses. The transcript appears once
 *  the status is `transcribed`, and it is final: no route edits it. */
export interface VoiceAnswerOut {
  id: string;
  status: VoiceAnswerStatus;
  transcript: string | null;
  /** The server's plain-language account of a failure, when there is one. */
  message?: string | null;
  /** The longest capture the server accepts, in seconds. Returned by the
   *  begin call so the recorder stops where the server's limit is. */
  max_seconds: number;
}

/** A blocked clipboard or drag action inside an answer field. The same
 *  words the lockdown layer reports, so one attempt reads identically in the
 *  answer's behaviour record and in the session's event log. */
export type BlockedFieldAction = "copy" | "cut" | "paste" | "drop";

/**
 * The hooks an answer field attaches to proctoring (proctoring spec 4.5).
 *
 * Every answer component calls these from its own handlers; it never reads
 * back what they recorded. Only the KEY NAME and the TIME reach the recorder,
 * and the recorder keeps offsets, not names: what was typed is the answer,
 * and the answer is stored separately.
 */
export interface ProctoringFieldHooks {
  onFieldFocus(): void;
  onFieldBlur(): void;
  /** Called on every keydown inside the field with the event's timestamp and
   *  whether it was a deletion (Backspace or Delete). */
  onKeyDown(timeStampMs: number, isDeletion: boolean): void;
  /**
   * A blocked copy, cut, paste or drop on this field. The implementation
   * counts it against this answer AND makes sure the session records one
   * blocked-action proctoring event carrying `kind`, so every attempt reaches
   * the proctoring report whichever layer caught it first. Every caller names
   * the kind it refused, the code editor included.
   */
  onBlockedAction(kind: BlockedFieldAction): void;
  /** A click on an MCQ option, for rapid-fire versus considered selection. */
  onOptionClick(timeStampMs: number): void;
  onScroll(): void;
}

/** Autosave state, rendered by every component the same way. `retrying`
 *  means the latest draft has not reached the server yet and another attempt
 *  is scheduled; it is said rather than hidden, because the server's copy is
 *  what a turn that runs out of time submits. */
export type AutosaveState = "idle" | "saving" | "saved" | "retrying";

/**
 * The common props contract (assessment spec 5.1). One component per format
 * implements exactly this, and the dispatcher passes exactly this.
 */
export interface AnswerComponentProps {
  question: QuestionOut;
  /** The question text the candidate reads. Rendered by the component only
   *  for the fill-blank format, whose prompt frames the template; every other
   *  component receives it for accessibility labels. */
  prompt: string;
  value: AnswerPayload | null;
  onChange(next: AnswerPayload): void;
  disabled: boolean;
  autosave: AutosaveState;
  fieldHooks: ProctoringFieldHooks;
  /** Ctrl/Cmd+Enter submits from any text field. */
  onSubmitShortcut(): void;
}

/**
 * The bridge the assessment player consumes (proctoring spec 8, 9).
 *
 * Provided by `components/proctoring/proctoring-context`, consumed by
 * `components/assessment/assessment-conversation`. The player never talks to
 * the proctoring session directly.
 */
export interface ProctoringBridge {
  /** consenting -> checking -> active -> ended */
  status: "consenting" | "checking" | "active" | "ended";
  sessionId: string | null;
  warningsUsed: number;
  maxWarnings: number;
  /** Plain-language reason, when the session has ended. */
  endedMessage: string | null;
  /**
   * True while the proctoring layer holds the assessment still: a blocking
   * warning on screen, or a camera or microphone loss the server has paused
   * the session for. The player freezes its countdown and refuses input while
   * it is true, and re-reads the server's clock the moment it turns false.
   *
   * Optional because the clock does not depend on it: the countdown reaching
   * zero is always checked against the server before anything is submitted,
   * so a bridge that never reports a pause can make the display less exact,
   * never make a turn end early.
   */
  paused?: boolean;
  /** The hooks for the field answering `questionKey`. Creating them starts a
   *  fresh capture for that key; the previous key's capture is kept until
   *  `collectAnswerBehaviour` reads it. */
  fieldHooksFor(questionKey: string): ProctoringFieldHooks;
  /** The recorded timings for the answer being submitted, or null when
   *  nothing was captured. Clears the capture. */
  collectAnswerBehaviour(questionKey: string): AnswerBehaviour | null;
  /** The conversation told us it ended (completed or terminated), so
   *  monitoring can stop and media can be released. */
  onConversationEnded(status: "completed" | "terminated"): void;
}

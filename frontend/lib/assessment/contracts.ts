/**
 * The shared contracts between the assessment player, the question-format
 * components and the proctoring client.
 *
 * ONE dispatcher, ONE props contract, six implementations (assessment spec 5.1),
 * and ONE bridge through which every answer component reaches proctoring
 * (proctoring spec 4.5). These types are the boundary; the two features are
 * built by different modules against them and neither imports the other's
 * internals.
 *
 * Every shape here mirrors a pydantic model on the backend:
 *   QuestionOut          schemas/assessments.QuestionOut
 *   AnswerPayload        services/assessment_formats/types.ANSWER_MODELS
 *   AnswerBehaviour      schemas/assessments.AnswerBehaviourIn
 *   ConversationTurn     schemas/assessments.ConversationOut
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

export interface CodingPayloadView {
  language: string;
  language_options: string[];
  starter_code: string;
  constraints: string;
}

export type QuestionPayloadView =
  | McqPayloadView
  | FillBlankPayloadView
  | CodingPayloadView
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

export interface ConversationTurn {
  conversation_id: string;
  status: "active" | "completed" | "terminated";
  prompt: string | null;
  progress_label: string;
  answered_questions: number;
  total_questions: number;
  is_reask: boolean;
  answer_message_id?: string | null;
  question?: QuestionOut | null;
  termination_message?: string | null;
}

/** The body of POST /conversations/{id}/respond. */
export interface RespondBody {
  answer: string;
  answer_payload?: AnswerPayload;
  paused_ms: number;
  behaviour?: AnswerBehaviour;
}

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
  /** A blocked paste, drop or clipboard read on this field. */
  onBlockedAction(): void;
  /** A click on an MCQ option, for rapid-fire versus considered selection. */
  onOptionClick(timeStampMs: number): void;
  onScroll(): void;
}

/** Autosave state, rendered by every component the same way. */
export type AutosaveState = "idle" | "saving" | "saved";

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
  /** The hooks for the field answering `questionKey`. Creating them starts a
   *  fresh capture for that key; the previous key's capture is kept until
   *  `collectAnswerBehaviour` reads it. */
  fieldHooksFor(questionKey: string): ProctoringFieldHooks;
  /** The recorded timings for the answer being submitted, or null when
   *  nothing was captured. Clears the capture. */
  collectAnswerBehaviour(questionKey: string): AnswerBehaviour | null;
  /** Milliseconds a blocking warning held the screen since the last call.
   *  Clears the counter. */
  consumePausedMs(): number;
  /** The conversation told us it ended (completed or terminated), so
   *  monitoring can stop and media can be released. */
  onConversationEnded(status: "completed" | "terminated"): void;
}

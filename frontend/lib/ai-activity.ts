/**
 * The AI activity wire contract, and the pure rules that read it.
 *
 * PROVENANCE
 * ----------
 * `ai-upgrade-spec-doc.md` "Case 3". The backend half is
 * `backend/app/services/activity/`: a workflow emits a typed event, and the
 * sentence is chosen there from the pair (task, event). Nothing on this side
 * writes copy, chooses a message or runs a timer, which is the whole point.
 * If a sentence ever needs changing it is changed in the catalogue, once, and
 * every screen that renders activity changes with it.
 *
 * WHY THE RULES BELOW ARE PURE FUNCTIONS AND NOT INSIDE THE HOOK
 * ---------------------------------------------------------------
 * They are the parts with real consequences: which of two concurrent
 * operations a payload belongs to, and whether an operation is still running.
 * A rule living inside a `useEffect` can only be tested by rendering, and the
 * failure it prevents (one run's status repainting over another's) is invisible
 * in a screenshot. They are exported so `ai-activity.test.tsx` can state them
 * directly.
 */

/** The sentence the backend rendered, plus where it came from. */
export interface AiActivityLine {
  operation_id: string;
  task: string;
  kind: string;
  sequence: number;
  /** Catalogue copy for this (task, event). Never model output. */
  text: string;
  /**
   * The workflow's own statement of what it found, when it computed one.
   * Empty when the milestone had nothing new to report.
   */
  detail: string;
  /** "event" | "task_default" | "generic". A visible fallback, not a hidden one. */
  source: string;
  terminal: boolean;
}

export type AiActivityState =
  | "idle"
  | "running"
  | "done"
  | "error"
  | "cancelled";

export interface AiActivityPayload {
  operation_id: string;
  task: string;
  /** What this operation is called where a heading is needed. */
  label: string;
  state: AiActivityState;
  line: AiActivityLine | null;
  log: AiActivityLine[];
  dropped_after_terminal: number;
}

/**
 * The transport's own view of the operation, when it has one.
 *
 * For a dispatched task this is `workers.status`: PENDING, PROGRESS, SUCCESS or
 * FAILURE. It is a separate question from the activity state, and both matter.
 * A task can fail in a way that never reaches the activity stream at all, for
 * example when the process is killed, and in that case the transport is the
 * only thing that knows the operation is over.
 */
/**
 * Any polled response that may carry an activity block.
 *
 * Optional on purpose. A workflow that has not been wired yet, and an older
 * backend during a rolling deploy, both answer without the field, and a screen
 * intersecting this type keeps working: the indicator renders nothing rather
 * than the screen inventing a status it was not given.
 */
export interface CarriesAiActivity {
  activity?: AiActivityPayload | null;
}

export type AiTransportState =
  | "PENDING"
  | "PROGRESS"
  | "SUCCESS"
  | "FAILURE"
  | "CANCELLED";

/**
 * Does this payload belong to the operation the caller is watching?
 *
 * Case 3 section 24. A payload for another operation is not merely useless, it
 * is actively wrong to render: a slower earlier run would repaint over a newer
 * one, and a finished run would hide a running one. An empty expected id means
 * nothing is being watched, so nothing is accepted.
 */
export function belongsToOperation(
  payload: AiActivityPayload | null | undefined,
  operationId: string
): boolean {
  if (!payload || !operationId) return false;
  return payload.operation_id === operationId;
}

/**
 * Later than what is already on screen?
 *
 * Polling can deliver responses out of order, and an older payload arriving
 * second would walk the activity backwards. Compared on the sequence the
 * BACKEND assigned as it reached each milestone, not on arrival time, because
 * arrival time is a fact about the network.
 */
export function isNewerLine(
  next: AiActivityLine | null | undefined,
  current: AiActivityLine | null | undefined
): boolean {
  if (!next) return false;
  if (!current) return true;
  if (next.operation_id !== current.operation_id) return true;
  return next.sequence > current.sequence;
}

/**
 * The state to show, from the activity block and the transport together.
 *
 * The transport WINS when it is terminal. That is the rule that keeps a failure
 * from leaving "comparing your experience" on screen (section 25): a task that
 * died without emitting anything still has a FAILURE recorded against its run,
 * and the activity stream, which lives inside the process that died, cannot
 * report on its own death.
 */
export function resolveActivityState(
  payload: AiActivityPayload | null | undefined,
  transport: AiTransportState | null | undefined
): AiActivityState {
  if (transport === "FAILURE") return "error";
  if (transport === "CANCELLED") return "cancelled";
  if (transport === "SUCCESS") {
    return payload && payload.state === "error" ? "error" : "done";
  }
  return payload ? payload.state : "idle";
}

/**
 * The one sentence to show.
 *
 * The workflow's own finding wins over the catalogue sentence when it has one,
 * because it is the more specific true statement about the same milestone:
 * "12 resumes matched on meaning" over "finding resumes that mean the same
 * thing as the role". Both were produced by the step that just ran.
 */
export function activitySentence(line: AiActivityLine | null | undefined): string {
  if (!line) return "";
  return line.detail || line.text;
}

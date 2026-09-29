/**
 * The AI activity status layer, for any ReadyPick screen running AI work.
 *
 * A screen that already polls its operation's status wires this in three lines:
 * take a view with `useAiActivity(runId)`, hand each polled response to
 * `report(response.activity, response.state)`, and render
 * `<AiActivityIndicator activity={...} />`. There is no per-feature component
 * and no per-feature copy: the sentences come from the backend catalogue in
 * `backend/app/services/activity/`, keyed by the workflow and the milestone.
 */
export { AiActivityIndicator } from "./ai-activity-indicator";
export {
  useAiActivity,
  APPEAR_AFTER_MS,
  MIN_LINE_MS,
  type AiActivityView,
} from "./use-ai-activity";

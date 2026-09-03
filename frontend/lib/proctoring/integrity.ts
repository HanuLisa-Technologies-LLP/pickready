/**
 * The integrity self-check (proctoring spec section 9).
 *
 * Periodically asks four questions of the client's own machinery: is the
 * camera stream live, is the microphone stream live, is the inference worker
 * still answering, are the lockdown handlers still on the page. The answers
 * travel on every heartbeat. A failure opens an episode; if it lasts
 * `integrity_failure_termination_seconds` the client reports
 * INTEGRITY_CHECK_FAILED with the duration and the server terminates; if it
 * recovers sooner the client reports INTEGRITY_CHECK_WARNING with the duration
 * and the server logs it. One event per episode, either way.
 *
 * What this cannot do, stated plainly: a candidate who can run code in the
 * page can make every probe answer true. The check catches the machinery
 * failing on its own and the ordinary interference; the server's heartbeat
 * gap and the report's honesty about gaps are the layer behind it.
 */
import type { MonitoringStatus } from "./api";
import type { EventDraft } from "./events";

export interface IntegrityOptions {
  intervalMs: number;
  terminationMs: number;
  probe: () => MonitoringStatus;
  onEvent: (draft: EventDraft) => void;
  now?: () => number;
}

export interface IntegrityHandle {
  stop(): void;
  /** The latest probe result, for the heartbeat body. */
  current(): MonitoringStatus;
  /** Run the probe now and apply the episode rules. */
  check(): MonitoringStatus;
}

export function failedParts(status: MonitoringStatus): string[] {
  return (Object.keys(status) as Array<keyof MonitoringStatus>).filter((key) => !status[key]);
}

export function startIntegrityCheck(options: IntegrityOptions): IntegrityHandle {
  const now = options.now ?? Date.now;
  let latest: MonitoringStatus = options.probe();
  let failedSince: number | null = null;
  let escalated = false;

  const check = (): MonitoringStatus => {
    latest = options.probe();
    const failed = failedParts(latest);
    const at = now();
    if (failed.length > 0) {
      if (failedSince === null) {
        failedSince = at;
        escalated = false;
      } else if (!escalated && at - failedSince >= options.terminationMs) {
        escalated = true;
        options.onEvent({
          event_type: "INTEGRITY_CHECK_FAILED",
          duration_ms: Math.round(at - failedSince),
          metadata: { failed },
        });
      }
    } else if (failedSince !== null) {
      if (!escalated) {
        options.onEvent({
          event_type: "INTEGRITY_CHECK_WARNING",
          duration_ms: Math.round(at - failedSince),
          metadata: {},
        });
      }
      failedSince = null;
      escalated = false;
    }
    return latest;
  };

  const timer = setInterval(check, options.intervalMs);
  return {
    check,
    current: () => latest,
    stop: () => clearInterval(timer),
  };
}

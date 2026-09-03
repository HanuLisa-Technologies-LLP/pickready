/**
 * The browser-side thresholds, exactly as the session response carries them.
 *
 * Every number the client's detectors, timers and capture use comes from
 * `SessionOut.config`, which the server projects from ONE frozen
 * `ProctoringConfig` (`services/proctoring/config.CLIENT_FIELDS`). A detector
 * that carried its own confidence threshold would drift from the one the
 * server records against the event, and the report would describe a rule
 * nobody was applying. So no module under `lib/proctoring/` holds a threshold
 * of its own; each takes the figure it needs from this object.
 *
 * `parseClientConfig` refuses a response missing any field rather than
 * substituting a default. A default here is a second copy of a number that
 * lives on the server, and the two would disagree the first time an operator
 * moved one of them.
 *
 * `lib/proctoring/model-assets.test.ts` reads the Python `CLIENT_FIELDS`
 * tuple and asserts `CLIENT_CONFIG_FIELDS` names the same set.
 */

export const CLIENT_CONFIG_FIELDS = [
  "max_warnings",
  "object_confidence_threshold",
  "object_consecutive_frames",
  "face_distance_threshold",
  "identity_check_interval_seconds",
  "obstruction_seconds",
  "obstruction_variance_threshold",
  "face_absent_moderate_seconds",
  "face_absent_extended_seconds",
  "focus_loss_ignore_under_seconds",
  "display_check_interval_seconds",
  "audio_chunk_seconds",
  "audio_max_chunk_bytes",
  "heartbeat_interval_seconds",
  "integrity_failure_termination_seconds",
  "camera_recovery_seconds",
  "sampling_fps_normal",
  "sampling_fps_confirming",
  "confirming_window_seconds",
  "sampling_fps_degraded",
  "low_light_luminance_threshold",
  "low_light_cooldown_seconds",
  "mouse_sample_hz",
  "max_keystroke_samples",
  "event_batch_max",
] as const;

export type ClientConfigField = (typeof CLIENT_CONFIG_FIELDS)[number];

export type ProctoringClientConfig = Record<ClientConfigField, number>;

export function parseClientConfig(raw: unknown): ProctoringClientConfig {
  if (!raw || typeof raw !== "object") {
    throw new Error("The monitoring session response carried no configuration.");
  }
  const source = raw as Record<string, unknown>;
  const config: Partial<ProctoringClientConfig> = {};
  for (const field of CLIENT_CONFIG_FIELDS) {
    const value = source[field];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`The monitoring configuration is missing "${field}".`);
    }
    config[field] = value;
  }
  return config as ProctoringClientConfig;
}

export const seconds = (value: number): number => value * 1000;

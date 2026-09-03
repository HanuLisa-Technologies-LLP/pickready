/**
 * The running monitoring session: every detector wired to the one event
 * queue, the one heartbeat and the one self-check.
 *
 * `ProctoringShell` owns the screens; this owns the machinery so the shell
 * stays a state machine over four phases. Everything here is started by
 * `start()` and released by `stop()`, and `stop()` is the only place media is
 * released: a session that ends for any reason, completion, termination or a
 * closed tab, stops every track and terminates both workers.
 *
 * The self-check's four answers come from the parts themselves: the camera's
 * track state, the microphone's track state, the inference client having
 * answered within one heartbeat interval, and the lockdown and focus handles
 * still being installed. Each is a check on the machinery, not on the
 * candidate; the header of lockdown.ts says what that is worth.
 */
import type { AnswerBehaviour, ProctoringFieldHooks } from "@/lib/assessment/contracts";

import {
  postAudioChunk,
  postEvents,
  postHeartbeat,
  type MonitoringStatus,
  type SessionOut,
  type TerminationOut,
  type WarningOut,
} from "./api";
import { AudioMonitor } from "./audio";
import { CameraMonitor } from "./camera";
import { BehaviourCapture } from "./capture";
import { seconds } from "./config";
import { DetectionRules } from "./detections";
import { watchDisplays, type DisplaysHandle } from "./displays";
import { EventQueue, type EventDraft } from "./events";
import { watchFocus, type FocusHandle } from "./focus";
import { startHeartbeat, type HeartbeatHandle } from "./heartbeat";
import { startIntegrityCheck, type IntegrityHandle } from "./integrity";
import { installLockdown, type LockdownHandle } from "./lockdown";
import type { InferenceClient } from "./worker-client";

export interface SessionMedia {
  camera: MediaStream;
  microphone: MediaStream;
  inference: InferenceClient;
}

export interface SessionCallbacks {
  onWarning: (warning: WarningOut, warningsUsed: number) => void;
  onTermination: (termination: TerminationOut) => void;
  /** The server said the session was already over (409). */
  onSessionEnded: (message: string) => void;
}

export class SessionRuntime {
  private readonly queue: EventQueue;
  private readonly rules: DetectionRules;
  private readonly capture: BehaviourCapture;
  private camera: CameraMonitor | null = null;
  private audio: AudioMonitor | null = null;
  private lockdown: LockdownHandle | null = null;
  private focus: FocusHandle | null = null;
  private displays: DisplaysHandle | null = null;
  private heartbeat: HeartbeatHandle | null = null;
  private integrity: IntegrityHandle | null = null;
  private stopped = false;

  constructor(
    private readonly session: SessionOut,
    private readonly media: SessionMedia,
    private readonly callbacks: SessionCallbacks
  ) {
    const { config } = session;
    this.rules = new DetectionRules(config);
    this.capture = new BehaviourCapture({
      maxKeystrokeSamples: config.max_keystroke_samples,
      mouseSampleHz: config.mouse_sample_hz,
    });
    this.queue = new EventQueue({
      batchMax: config.event_batch_max,
      // The retry backoff is capped at one heartbeat interval. A queue that
      // backed off further would be silent for longer than the server's own
      // liveness window, so the server would be recording a monitoring gap
      // while this client was merely waiting to try again.
      maxBackoffMs: seconds(config.heartbeat_interval_seconds),
      post: (events) => postEvents(session.session_id, events),
      onWarning: callbacks.onWarning,
      onTermination: (termination) => this.terminated(termination),
      onSessionEnded: (message) => this.ended(message),
    });
  }

  readonly emit = (draft: EventDraft): void => {
    this.queue.enqueue(draft);
  };

  start(): void {
    const { config } = this.session;
    this.lockdown = installLockdown({ onBlocked: (action) => this.emit({ event_type: "BLOCKED_ACTION_ATTEMPTED", metadata: { action } }) });
    this.focus = watchFocus({ ignoreUnderMs: seconds(config.focus_loss_ignore_under_seconds), onEvent: this.emit });
    this.displays = watchDisplays({ intervalMs: seconds(config.display_check_interval_seconds), onEvent: this.emit });

    this.camera = new CameraMonitor({
      config,
      inference: this.media.inference,
      rules: this.rules,
      onEvent: this.emit,
      identityChecks: true,
    });
    void this.camera.start(this.media.camera);

    this.audio = new AudioMonitor({
      chunkSeconds: config.audio_chunk_seconds,
      maxChunkBytes: config.audio_max_chunk_bytes,
      uploadEnabled: this.session.audio_analysis_available,
      retryIntervalMs: seconds(config.heartbeat_interval_seconds),
      upload: (chunk) => postAudioChunk(this.session.session_id, chunk),
      onEvent: this.emit,
      onWarning: this.callbacks.onWarning,
      onTermination: (termination) => this.terminated(termination),
      onSessionEnded: (message) => this.ended(message),
    });
    void this.audio.start(this.media.microphone);

    this.integrity = startIntegrityCheck({
      intervalMs: seconds(config.heartbeat_interval_seconds),
      terminationMs: seconds(config.integrity_failure_termination_seconds),
      probe: () => this.probe(),
      onEvent: this.emit,
    });
    this.heartbeat = startHeartbeat({
      intervalMs: seconds(config.heartbeat_interval_seconds),
      post: (body) => postHeartbeat(this.session.session_id, body),
      monitoring: () => this.integrity?.check() ?? this.probe(),
      identityMatched: () => this.rules.identityMatched,
      onTermination: (termination) => this.terminated(termination),
      onSessionEnded: (message) => this.ended(message),
    });
  }

  probe(): MonitoringStatus {
    const withinMs = seconds(this.session.config.heartbeat_interval_seconds);
    return {
      camera: this.camera?.status().live ?? false,
      microphone: this.audio?.live() ?? false,
      models: this.media.inference.responsive(withinMs),
      handlers: Boolean(this.lockdown?.installed() && this.focus?.installed()),
    };
  }

  fieldHooksFor(questionKey: string): ProctoringFieldHooks {
    return this.capture.hooksFor(questionKey);
  }

  collectAnswerBehaviour(questionKey: string): AnswerBehaviour | null {
    return this.capture.collect(questionKey);
  }

  requestFullscreen(): Promise<boolean> {
    return this.focus?.requestFullscreen() ?? Promise.resolve(false);
  }

  /** Send whatever is queued now. Called before the session is torn down. */
  flush(): Promise<void> {
    return this.queue.flush();
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.heartbeat?.stop();
    this.integrity?.stop();
    this.queue.stop();
    this.camera?.stop();
    this.audio?.stop();
    this.displays?.release();
    this.focus?.release();
    this.lockdown?.release();
    this.capture.release();
    this.media.inference.terminate();
    if (typeof document !== "undefined" && document.fullscreenElement !== null) {
      void document.exitFullscreen().catch(() => undefined);
    }
  }

  private terminated(termination: TerminationOut): void {
    if (this.stopped) return;
    this.stop();
    this.callbacks.onTermination(termination);
  }

  private ended(message: string): void {
    if (this.stopped) return;
    this.stop();
    this.callbacks.onSessionEnded(message);
  }
}

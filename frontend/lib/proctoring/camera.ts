/**
 * The camera: stream, frame sampling, the rate policy and stream recovery
 * (proctoring spec 3.6, 4.1, 4.6).
 *
 * THE FRAME'S LIFE. On each tick a bitmap is created from the video element,
 * transferred to the inference worker, and the worker closes it after
 * drawing it once. This thread never holds a frame past the `postMessage`
 * call, never draws one to a visible canvas, never encodes one, and the video
 * element it samples from is never attached to the document. What comes back
 * is `FrameDetections`, which `DetectionRules` turns into event requests.
 *
 * THE RATE. `sampling_fps_normal` by default; `sampling_fps_confirming` for
 * `confirming_window_seconds` after the rules say something may be present;
 * `sampling_fps_degraded` for the rest of the session once the device has
 * shown it cannot keep up. "Cannot keep up" is measured, not guessed: over one
 * heartbeat interval's worth of frames the worker's mean inference time
 * exceeded the normal frame interval. The downgrade is reported ONCE as
 * SESSION_QUALITY_DEGRADED, because a slow laptop is context for the report
 * and never a reason to stop a candidate (spec 3.6).
 *
 * A DEAD STREAM. The track ending is either the candidate revoking the
 * permission (CAMERA_PERMISSION_LOST, at once) or the device failing (retry
 * every heartbeat interval; CAMERA_STREAM_INTERRUPTED with the duration if it
 * comes back within `camera_recovery_seconds`, CAMERA_STREAM_FAILED with the
 * duration at that threshold). The permission API says which, where the
 * browser offers it; where it does not, a re-acquire refused with
 * NotAllowedError is the same answer.
 */
import type { ProctoringClientConfig } from "./config";
import { seconds } from "./config";
import { DetectionRules, type FrameDetections } from "./detections";
import type { EventDraft } from "./events";
import type { InferenceClient } from "./worker-client";

type CameraConfig = Pick<
  ProctoringClientConfig,
  | "sampling_fps_normal"
  | "sampling_fps_confirming"
  | "confirming_window_seconds"
  | "sampling_fps_degraded"
  | "identity_check_interval_seconds"
  | "camera_recovery_seconds"
  | "heartbeat_interval_seconds"
>;

export interface CameraOptions {
  config: CameraConfig;
  inference: InferenceClient;
  rules: DetectionRules;
  onEvent: (draft: EventDraft) => void;
  /** Every frame's detections, when the caller wants them. The system check
   *  uses it to read the latest frame; the session does not, because the
   *  rules are the only consumer there. */
  onFrame?: (frame: FrameDetections) => void;
  /** Whether the identity comparison runs on this session. Off during the
   *  system check, where the baseline is taken instead. */
  identityChecks: boolean;
  navigator?: Navigator;
  document?: Document;
  now?: () => number;
}

export interface CameraStatus {
  live: boolean;
  measuredFps: number | null;
  degraded: boolean;
  width: number | null;
  height: number | null;
}

export async function openCamera(navigator: Navigator = window.navigator): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
}

export function isPermissionDenied(error: unknown): boolean {
  return error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "SecurityError");
}

export class CameraMonitor {
  private stream: MediaStream | null = null;
  private readonly video: HTMLVideoElement;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private inFlight = false;
  private confirmingUntil = 0;
  private degraded = false;
  private lastIdentityCheck: number;
  private readonly inferenceTimes: number[] = [];
  private readonly windowFrames: number;
  private frames = 0;
  private startedAt: number;
  private failureSince: number | null = null;
  private failureReported = false;
  private readonly nav: Navigator;
  private readonly now: () => number;
  private permissionStatus: PermissionStatus | null = null;

  constructor(private readonly options: CameraOptions) {
    this.nav = options.navigator ?? window.navigator;
    this.now = options.now ?? Date.now;
    const doc = options.document ?? document;
    this.video = doc.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.windowFrames = options.config.sampling_fps_normal * options.config.heartbeat_interval_seconds;
    this.startedAt = this.now();
    this.lastIdentityCheck = this.startedAt;
  }

  async start(stream?: MediaStream): Promise<void> {
    this.stream = stream ?? (await openCamera(this.nav));
    await this.attach(this.stream);
    this.watchPermission();
    this.startedAt = this.now();
    this.frames = 0;
    this.schedule();
  }

  status(): CameraStatus {
    const track = this.stream?.getVideoTracks()[0];
    const settings = track?.getSettings();
    const elapsed = (this.now() - this.startedAt) / 1000;
    return {
      live: Boolean(track && track.readyState === "live"),
      measuredFps: elapsed > 0 && this.frames > 0 ? this.frames / elapsed : null,
      degraded: this.degraded,
      width: settings?.width ?? null,
      height: settings?.height ?? null,
    };
  }

  /** One frame for the identity worker, outside the sampling loop. Used by
   *  the system check to take the baseline. */
  async snapshot(): Promise<ImageBitmap> {
    return createImageBitmap(this.video);
  }

  /**
   * Stop sampling and let go of the stream WITHOUT stopping its tracks.
   *
   * The system check hands its camera to the session, so the tracks have to
   * outlive the monitor that opened them. Stopping them here and reopening
   * would show the candidate a second permission prompt and a second camera
   * light for the same session.
   */
  detach(): MediaStream | null {
    this.stopped = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (this.permissionStatus) this.permissionStatus.onchange = null;
    const stream = this.stream;
    const track = stream?.getVideoTracks()[0];
    if (track) track.onended = null;
    this.stream = null;
    this.video.srcObject = null;
    return stream;
  }

  stop(): void {
    this.detach()?.getTracks().forEach((track) => track.stop());
  }

  private async attach(stream: MediaStream): Promise<void> {
    this.video.srcObject = stream;
    await this.video.play();
    const track = stream.getVideoTracks()[0];
    if (track) track.onended = () => void this.onTrackEnded();
  }

  private watchPermission(): void {
    if (!this.nav.permissions) return;
    this.nav.permissions
      .query({ name: "camera" as PermissionName })
      .then((status) => {
        this.permissionStatus = status;
        status.onchange = () => {
          if (status.state === "denied") this.permissionLost();
        };
      })
      .catch(() => {
        // The browser does not expose the camera permission by name. The
        // track's `ended` handler and a refused re-acquire cover the case.
      });
  }

  private permissionLost(): void {
    if (this.stopped) return;
    this.options.onEvent({ event_type: "CAMERA_PERMISSION_LOST", metadata: {} });
    this.stop();
  }

  private async onTrackEnded(): Promise<void> {
    if (this.stopped) return;
    if (this.permissionStatus?.state === "denied") {
      this.permissionLost();
      return;
    }
    this.failureSince = this.now();
    this.failureReported = false;
    await this.recover();
  }

  private async recover(): Promise<void> {
    const failedAt = this.failureSince;
    if (this.stopped || failedAt === null) return;
    try {
      const stream = await openCamera(this.nav);
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = stream;
      await this.attach(stream);
      this.options.onEvent({
        event_type: "CAMERA_STREAM_INTERRUPTED",
        duration_ms: Math.round(this.now() - failedAt),
        metadata: {},
      });
      this.failureSince = null;
      return;
    } catch (error) {
      if (isPermissionDenied(error)) {
        this.permissionLost();
        return;
      }
    }
    const down = this.now() - failedAt;
    if (!this.failureReported && down >= seconds(this.options.config.camera_recovery_seconds)) {
      this.failureReported = true;
      this.options.onEvent({
        event_type: "CAMERA_STREAM_FAILED",
        duration_ms: Math.round(down),
        metadata: {},
      });
      return;
    }
    setTimeout(() => void this.recover(), seconds(this.options.config.heartbeat_interval_seconds));
  }

  private currentIntervalMs(): number {
    const { config } = this.options;
    if (this.degraded) return 1000 / config.sampling_fps_degraded;
    if (this.now() < this.confirmingUntil) return 1000 / config.sampling_fps_confirming;
    return 1000 / config.sampling_fps_normal;
  }

  private schedule(): void {
    if (this.stopped) return;
    this.timer = setTimeout(() => void this.tick(), this.currentIntervalMs());
  }

  private async tick(): Promise<void> {
    if (this.stopped) return;
    const track = this.stream?.getVideoTracks()[0];
    if (this.inFlight || !track || track.readyState !== "live" || this.video.readyState < this.video.HAVE_CURRENT_DATA) {
      this.schedule();
      return;
    }
    this.inFlight = true;
    const at = this.now();
    try {
      const wantsIdentity =
        this.options.identityChecks &&
        at - this.lastIdentityCheck >= seconds(this.options.config.identity_check_interval_seconds);
      const [frame, identityFrame] = await Promise.all([
        createImageBitmap(this.video),
        wantsIdentity ? createImageBitmap(this.video) : Promise.resolve(null),
      ]);
      const detections = await this.options.inference.detect(frame, at);
      this.frames += 1;
      this.recordInference(detections.inferenceMs);
      const observed: FrameDetections = {
        at,
        objects: detections.objects,
        faces: detections.faces,
        persons: detections.persons,
        luminance: detections.luminance,
        variance: detections.variance,
      };
      this.options.onFrame?.(observed);
      const outcome = this.options.rules.observe(observed);
      for (const event of outcome.events) this.options.onEvent(event);
      if (outcome.confirming) {
        this.confirmingUntil = at + seconds(this.options.config.confirming_window_seconds);
      }
      if (identityFrame) {
        this.lastIdentityCheck = at;
        const distance = await this.options.inference.check(identityFrame);
        for (const event of this.options.rules.observeIdentity(distance)) this.options.onEvent(event);
      }
    } catch {
      // A frame the worker could not process. The integrity check watches
      // for the worker going quiet; one bad frame is not that.
    } finally {
      this.inFlight = false;
      this.schedule();
    }
  }

  private recordInference(inferenceMs: number): void {
    if (this.degraded) return;
    this.inferenceTimes.push(inferenceMs);
    if (this.inferenceTimes.length < this.windowFrames) return;
    const mean = this.inferenceTimes.reduce((sum, value) => sum + value, 0) / this.inferenceTimes.length;
    this.inferenceTimes.length = 0;
    if (mean > 1000 / this.options.config.sampling_fps_normal) {
      this.degraded = true;
      this.options.onEvent({
        event_type: "SESSION_QUALITY_DEGRADED",
        metadata: { note: "inference_below_normal_rate", mean_inference_ms: Math.round(mean) },
      });
    }
  }
}

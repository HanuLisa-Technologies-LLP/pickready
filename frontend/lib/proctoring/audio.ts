/**
 * The microphone: voice activity and the audio chunk (proctoring spec 3.4).
 *
 * THE ONE PIECE OF MEDIA THAT LEAVES THE BROWSER, and the rules around it:
 * chunks of `audio_chunk_seconds`, uploaded ONLY when speech was heard in
 * that chunk and ONLY when the deployment has an analysis service
 * (`audio_analysis_available`); silence is never uploaded; a chunk over
 * `audio_max_chunk_bytes` is not uploaded either. The chunk goes to the one
 * route that analyses it in memory and destroys it, and this thread drops its
 * reference the moment the request is made. Nothing is played back, stored,
 * or logged.
 *
 * WHY THE RECORDER IS RESTARTED RATHER THAN SLICED. `MediaRecorder` with a
 * timeslice hands back fragments of one continuous file, and only the first
 * carries the container header, so every later fragment is undecodable on
 * its own. Stopping and restarting every chunk produces complete files the
 * analysis service can decode independently.
 *
 * VOICE ACTIVITY. An AnalyserNode is polled for the RMS energy of the signal;
 * a chunk is "speech present" when its loudest reading exceeds its quietest
 * by `SPEECH_ENERGY_RATIO`. That is the VAD's own calibration, the one
 * number in this client that is not on the session config, because the
 * config has no field for it; it is named here so it is visible and it errs
 * toward uploading, because a false "silence" would hide a second voice and
 * a false "speech" costs one analysis call. The SERVER decides speaker
 * counts; this module only decides whether to ask.
 */
import type { AudioChunkOut, TerminationOut, WarningOut } from "./api";
import { isSessionEnded } from "./api";
import type { EventDraft } from "./events";
import { isPermissionDenied } from "./camera";

/** See the header. Quietest-to-loudest RMS ratio within one chunk. */
export const SPEECH_ENERGY_RATIO = 4;

/**
 * How often the analyser is read. A measurement resolution, not a threshold:
 * speech is hundreds of milliseconds long, and reading ten times a second
 * cannot miss it while costing nothing the assessment would notice.
 */
export const VAD_POLL_MS = 100;

export interface AudioOptions {
  chunkSeconds: number;
  maxChunkBytes: number;
  /** `SessionOut.audio_analysis_available`. When false nothing is recorded. */
  uploadEnabled: boolean;
  retryIntervalMs: number;
  upload: (chunk: Blob) => Promise<AudioChunkOut>;
  onEvent: (draft: EventDraft) => void;
  onWarning: (warning: WarningOut, warningsUsed: number) => void;
  onTermination: (termination: TerminationOut) => void;
  onSessionEnded: (message: string) => void;
  navigator?: Navigator;
}

export async function openMicrophone(navigator: Navigator = window.navigator): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({ audio: true, video: false });
}

function preferredMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const candidate of ["audio/webm;codecs=opus", "audio/webm"]) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return undefined;
}

export class AudioMonitor {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private recorder: MediaRecorder | null = null;
  private vadTimer: ReturnType<typeof setInterval> | null = null;
  private chunkTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private quietest = Number.POSITIVE_INFINITY;
  private loudest = 0;
  private readonly nav: Navigator;
  private permissionStatus: PermissionStatus | null = null;

  constructor(private readonly options: AudioOptions) {
    this.nav = options.navigator ?? window.navigator;
  }

  async start(stream?: MediaStream): Promise<void> {
    this.stream = stream ?? (await openMicrophone(this.nav));
    this.attach(this.stream);
    this.watchPermission();
  }

  live(): boolean {
    const track = this.stream?.getAudioTracks()[0];
    return Boolean(track && track.readyState === "live");
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    if (this.permissionStatus) this.permissionStatus.onchange = null;
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.ondataavailable = null;
      this.recorder.stop();
    }
    this.recorder = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    void this.context?.close();
    this.context = null;
    this.analyser = null;
  }

  private attach(stream: MediaStream): void {
    const track = stream.getAudioTracks()[0];
    if (track) track.onended = () => void this.onTrackEnded();
    if (!this.options.uploadEnabled) return;
    this.context = new AudioContext();
    this.analyser = this.context.createAnalyser();
    this.context.createMediaStreamSource(stream).connect(this.analyser);
    this.vadTimer = setInterval(() => this.sampleEnergy(), VAD_POLL_MS);
    this.startChunk(stream);
  }

  private sampleEnergy(): void {
    if (!this.analyser) return;
    const buffer = new Float32Array(this.analyser.fftSize);
    this.analyser.getFloatTimeDomainData(buffer);
    let total = 0;
    for (const sample of buffer) total += sample * sample;
    const rms = Math.sqrt(total / buffer.length);
    this.quietest = Math.min(this.quietest, rms);
    this.loudest = Math.max(this.loudest, rms);
  }

  private speechHeard(): boolean {
    if (!Number.isFinite(this.quietest)) return false;
    return this.loudest > this.quietest * SPEECH_ENERGY_RATIO;
  }

  private startChunk(stream: MediaStream): void {
    if (this.stopped) return;
    const mimeType = preferredMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    this.recorder = recorder;
    this.quietest = Number.POSITIVE_INFINITY;
    this.loudest = 0;
    recorder.ondataavailable = (event) => {
      const speech = this.speechHeard();
      const chunk = event.data;
      if (speech && chunk.size > 0 && chunk.size <= this.options.maxChunkBytes) {
        void this.send(chunk);
      }
      // `chunk` is not referenced after this handler returns. Whether or
      // not it was sent, nothing holds it.
    };
    recorder.onstop = () => {
      if (!this.stopped && this.stream === stream) this.startChunk(stream);
    };
    recorder.start();
    this.chunkTimer = setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, this.options.chunkSeconds * 1000);
  }

  private async send(chunk: Blob): Promise<void> {
    try {
      const result = await this.options.upload(chunk);
      if (result.warning) this.options.onWarning(result.warning, result.warnings_used);
      if (result.termination) {
        this.stop();
        this.options.onTermination(result.termination);
      }
    } catch (error) {
      if (isSessionEnded(error)) {
        this.stop();
        this.options.onSessionEnded((error as Error).message);
      }
      // Any other failure loses this chunk's analysis only. The next chunk
      // is a fresh request, and a missed second voice in one chunk is not a
      // reason to surface an error to the candidate.
    }
  }

  private watchPermission(): void {
    if (!this.nav.permissions) return;
    this.nav.permissions
      .query({ name: "microphone" as PermissionName })
      .then((status) => {
        this.permissionStatus = status;
        status.onchange = () => {
          if (status.state === "denied") this.permissionLost();
        };
      })
      .catch(() => {
        // Not exposed by name in this browser; the track's `ended` handler
        // and a refused re-acquire cover it.
      });
  }

  private permissionLost(): void {
    if (this.stopped) return;
    this.options.onEvent({ event_type: "MIC_PERMISSION_LOST", metadata: {} });
    this.stop();
  }

  private async onTrackEnded(): Promise<void> {
    if (this.stopped) return;
    if (this.permissionStatus?.state === "denied") {
      this.permissionLost();
      return;
    }
    this.clearTimers();
    try {
      const stream = await openMicrophone(this.nav);
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = stream;
      void this.context?.close();
      this.context = null;
      this.attach(stream);
    } catch (error) {
      if (isPermissionDenied(error)) {
        this.permissionLost();
        return;
      }
      // The device is gone but the permission stands. The self-check reports
      // the microphone down and the session quality note says so.
      this.options.onEvent({
        event_type: "SESSION_QUALITY_DEGRADED",
        metadata: { note: "microphone_unavailable" },
      });
      setTimeout(() => void this.onTrackEnded(), this.options.retryIntervalMs);
    }
  }

  private clearTimers(): void {
    if (this.vadTimer !== null) clearInterval(this.vadTimer);
    if (this.chunkTimer !== null) clearTimeout(this.chunkTimer);
    this.vadTimer = null;
    this.chunkTimer = null;
  }
}

/**
 * The proctored session's recording: capture, upload in parts, finalize.
 *
 * Provenance: the owner ruling of 2026-09-22, which reversed principle P1 and
 * required that the assessment video be compressed and stored securely in S3,
 * linked to the candidate assessment, and the Phase 3 plan (2026-09-24, N2),
 * which found the first version of this file holding the WHOLE session in
 * browser memory and posting it once at the end. With twenty-minute coding
 * questions a session passes two hours; the tab carried all of it, the API
 * read up to a gibibyte of it into one request, and a crashed tab lost all of
 * it.
 *
 * WHAT CHANGED. The recording now leaves the browser as it is made. Each
 * SEGMENT is one multipart upload on the server; `MediaRecorder` hands back a
 * blob every `TIMESLICE_MS`, the blobs collect until they reach the server's
 * `part_min_bytes`, and each full part is PUT at once. What this tab holds is
 * therefore about one part, plus whatever is waiting on a slow network, and a
 * crash loses the tail since the last part rather than the session. The last
 * part of a segment may be smaller than the floor, and it is sent marked as
 * the last (`final`); every other part may not, because the object store
 * refuses one that is.
 *
 * A NEW SEGMENT AFTER EVERY DEVICE RECOVERY. A recorder is bound to the tracks
 * it was built on, and a camera or microphone that went away and came back is
 * a new track. `restart` closes the running segment (its last part and its
 * completion are sent) and opens a fresh one on the new streams. The server
 * stitches the segments in order when the recording is finalized. Segment
 * ordinals are the server's; the network calls are strictly serial, so the
 * order the server sees is the order they were recorded in.
 *
 * THE ENCODER IS BOUNDED BY THE SERVER'S NUMBERS. The bitrates and the frame
 * size come from `session-media/start`. The size is applied to a CLONE of the
 * camera track, so the detectors keep sampling the camera at the resolution
 * the system check measured while the recording is scaled down.
 *
 * IT RECORDS THE CAMERA AND THE MICROPHONE, AND NOTHING ELSE. Not the screen:
 * a screen capture would carry whatever else is on the candidate's display,
 * which is their own data and nobody's business here. Unchanged since
 * 2026-09-02.
 *
 * A FAILURE HERE NEVER ENDS AN ASSESSMENT. A transport failure is retried
 * with backoff; a refusal the server means (a 409 because the recording is
 * closed, any other client error) or a network that stays down past the
 * retries or past `MAX_PENDING_PARTS` of backlog stops the RECORDING, reports
 * why through `onProblem`, and leaves the candidate answering. Whatever
 * segments were completed are still finalized, and the server's hourly sweep
 * completes a segment a closed tab left open. The recording row keeps the
 * honest status the hiring team reads.
 */
import { ApiError, NETWORK_ERROR } from "@/lib/api";

import type { SessionMediaStartOut } from "./api";

/** The network half, injected so a test drives the recorder without one. */
export interface RecordingTransport {
  /** `sourceFormat` is the MediaRecorder's MIME type. */
  openSegment(sourceFormat: string): Promise<{ segment_id: string }>;
  /** `final` marks the segment's last part, the only one that may be
   *  smaller than the server's `part_min_bytes`. */
  uploadPart(segmentId: string, partNumber: number, body: Blob, final: boolean): Promise<void>;
  completeSegment(segmentId: string): Promise<void>;
  finalize(): Promise<void>;
}

export interface RecorderOptions {
  recording: SessionMediaStartOut;
  transport: RecordingTransport;
  /** Reported, never thrown: see the header. */
  onProblem?: (reason: string) => void;
  recorderFactory?: (stream: MediaStream, options: MediaRecorderOptions) => MediaRecorder;
  /** The container probe, injected for the same reason `recorderFactory` is:
   *  `MediaRecorder` is a browser global and a test environment has none, so
   *  without this seam every test here would exercise the cannot-record
   *  branch and prove nothing. */
  isTypeSupported?: (type: string) => boolean;
  /** Builds the stream the recorder records. A seam for the same reason:
   *  jsdom has no `MediaStream`. */
  streamFactory?: (tracks: MediaStreamTrack[]) => MediaStream;
  /** Waits between retries. A seam so a test does not sleep for real. */
  sleep?: (ms: number) => Promise<void>;
}

/**
 * The container the browser will actually give us, in preference order.
 *
 * Asked rather than assumed: `isTypeSupported` is the only honest answer, and
 * a hardcoded `video/webm;codecs=vp9` produces an empty recording on a
 * browser that does not have it, with no error anywhere. The server derives
 * the stored object's extension from a FIXED table keyed on whatever we
 * report, so an unexpected type cannot smuggle anything into an object key.
 */
const PREFERRED_TYPES = [
  "video/webm;codecs=vp9,opus",
  "video/webm;codecs=vp8,opus",
  "video/webm",
  "video/mp4",
] as const;

export function pickMimeType(
  isSupported: (type: string) => boolean = (type) =>
    typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)
): string | null {
  for (const type of PREFERRED_TYPES) {
    if (isSupported(type)) return type;
  }
  return null;
}

/**
 * How often `MediaRecorder` hands back a blob. Transport pacing, not a
 * threshold: short enough that a part fills and leaves promptly, long enough
 * that the blobs waiting for a part are a handful rather than hundreds.
 */
export const TIMESLICE_MS = 10_000;

/**
 * The waits between retries of one failed request, and so also how many
 * retries there are: about two minutes in all, the same span as the device
 * grace, after which a network that is still down has cost the recording and
 * nothing else. Transport pacing, not a behavioural threshold.
 */
export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16_000, 30_000, 30_000, 30_000] as const;

/**
 * How much of the recording may wait in this tab for the network, in parts of
 * the server's maximum size. Past it the recording stops rather than grow
 * without bound in the memory of a machine that is also running the
 * detectors and the assessment.
 */
export const MAX_PENDING_PARTS = 4;

interface Segment {
  recorder: MediaRecorder | null;
  /** The cloned tracks this segment records, stopped when it closes. */
  tracks: MediaStreamTrack[];
  buffer: Blob[];
  buffered: number;
  partsQueued: number;
  segmentId: string | null;
  closing: boolean;
  closed: boolean;
  resolveClosed: () => void;
  whenClosed: Promise<void>;
}

/** A failure a retry can repair: the request never arrived, or the server
 *  said "not now" rather than "no". */
function isRetryable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    error.status === NETWORK_ERROR ||
    error.status === 408 ||
    error.status === 429 ||
    error.status >= 500
  );
}

function describe(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export class SessionRecorder {
  private readonly mimeType: string | null;
  private segment: Segment | null = null;
  private chain: Promise<void> = Promise.resolve();
  private pendingBytes = 0;
  private completedSegments = 0;
  private stopped = false;
  private failed = false;
  private settled: Promise<void> = Promise.resolve();
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(private readonly options: RecorderOptions) {
    this.mimeType = options.isTypeSupported
      ? pickMimeType(options.isTypeSupported)
      : pickMimeType();
    this.sleep =
      options.sleep ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  /** Begin the first segment on the session's two streams. */
  start(camera: MediaStream, microphone: MediaStream): void {
    if (this.mimeType === null) {
      this.fail("This browser cannot record the assessment, so no recording was made.");
      return;
    }
    if (this.stopped || this.failed || this.segment !== null) return;
    this.begin(camera, microphone);
  }

  /**
   * Close the running segment and begin a new one on the given streams.
   * Called after a lost camera or microphone is reacquired.
   */
  restart(camera: MediaStream, microphone: MediaStream): void {
    if (this.stopped || this.failed || this.mimeType === null) return;
    this.end();
    this.begin(camera, microphone);
  }

  /**
   * Stop recording and hand the rest over. Returns immediately; `finished()`
   * is how a caller waits. Called BEFORE the session releases its tracks,
   * because a recorder whose tracks have already ended produces nothing more.
   */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    const closing = this.end();
    this.settled = closing.then(() => this.chain).then(() => this.finish());
  }

  /** Resolves once every part, every completion and the finalize have been
   *  attempted. */
  finished(): Promise<void> {
    return this.settled;
  }

  /** Bytes captured and not yet accepted by the server: the tab's share of
   *  the recording at this moment. */
  heldBytes(): number {
    return this.pendingBytes + (this.segment?.buffered ?? 0);
  }

  private begin(camera: MediaStream, microphone: MediaStream): void {
    let resolveClosed: () => void = () => undefined;
    const whenClosed = new Promise<void>((resolve) => {
      resolveClosed = resolve;
    });
    const segment: Segment = {
      recorder: null,
      tracks: [],
      buffer: [],
      buffered: 0,
      partsQueued: 0,
      segmentId: null,
      closing: false,
      closed: false,
      resolveClosed,
      whenClosed,
    };
    this.segment = segment;
    void this.open(segment, camera, microphone);
  }

  private async open(segment: Segment, camera: MediaStream, microphone: MediaStream): Promise<void> {
    const { recording } = this.options;
    const video = camera.getVideoTracks()[0]?.clone() ?? null;
    const audio = microphone.getAudioTracks()[0]?.clone() ?? null;
    segment.tracks = [video, audio].filter((track): track is MediaStreamTrack => track !== null);
    if (video) {
      try {
        await video.applyConstraints({
          width: { max: recording.max_width },
          height: { max: recording.max_height },
        });
      } catch (error) {
        // The bitrate cap below still bounds the size; only the frame size is
        // the camera's own. Said, not hidden.
        this.options.onProblem?.(
          "The recording could not be scaled down, so it keeps the camera's own size: " +
            describe(error, "the constraint was refused")
        );
      }
    }
    // Closed while the tracks were being prepared: nothing was recorded.
    if (segment.closing || this.stopped || this.failed) {
      this.close(segment);
      return;
    }
    const stream = this.options.streamFactory
      ? this.options.streamFactory(segment.tracks)
      : new MediaStream(segment.tracks);
    const factory =
      this.options.recorderFactory ??
      ((source: MediaStream, opts: MediaRecorderOptions) => new MediaRecorder(source, opts));
    let recorder: MediaRecorder;
    try {
      recorder = factory(stream, {
        mimeType: this.mimeType ?? undefined,
        videoBitsPerSecond: recording.video_bits_per_second,
        audioBitsPerSecond: recording.audio_bits_per_second,
      });
    } catch (error) {
      this.close(segment);
      this.fail(describe(error, "The assessment recorder could not be opened."));
      return;
    }
    recorder.ondataavailable = (event: BlobEvent) => this.take(segment, event.data);
    // Fired both for a stop this class asked for and for one the browser
    // decided on, which is what happens when every track the recorder holds
    // has ended. Either way the segment is over and what it captured is sent.
    recorder.onstop = () => this.close(segment);
    recorder.onerror = () => {
      this.options.onProblem?.(
        "The assessment recording stopped unexpectedly. Your answers are unaffected."
      );
    };
    segment.recorder = recorder;
    recorder.start(TIMESLICE_MS);
  }

  /** Ask the running segment to close; resolves when it has queued its tail. */
  private end(): Promise<void> {
    const segment = this.segment;
    this.segment = null;
    if (segment === null) return Promise.resolve();
    segment.closing = true;
    const recorder = segment.recorder;
    if (recorder !== null && recorder.state !== "inactive") {
      recorder.stop();
    } else if (recorder !== null) {
      this.close(segment);
    }
    // No recorder yet: `open` sees `closing` when its tracks are ready.
    return segment.whenClosed;
  }

  private take(segment: Segment, blob: Blob | undefined): void {
    if (!blob || blob.size === 0 || this.failed) return;
    segment.buffer.push(blob);
    segment.buffered += blob.size;
    this.cut(segment, false);
    this.checkBacklog();
  }

  /** Turn what is buffered into parts. A part is at least the floor and at
   *  most the ceiling; only the final cut of a segment may send less. */
  private cut(segment: Segment, final: boolean): void {
    const { part_min_bytes: floor, part_max_bytes: ceiling } = this.options.recording;
    if (segment.buffered === 0 || (!final && segment.buffered < floor)) return;
    const type = this.mimeType ?? undefined;
    let remaining = new Blob(segment.buffer, { type });
    segment.buffer = [];
    segment.buffered = 0;
    while (remaining.size > 0 && (final || remaining.size >= floor)) {
      const size = Math.min(remaining.size, ceiling);
      const last = final && size === remaining.size;
      this.queuePart(segment, remaining.slice(0, size, type), last);
      remaining = remaining.slice(size, remaining.size, type);
    }
    if (remaining.size > 0) {
      segment.buffer = [remaining];
      segment.buffered = remaining.size;
    }
  }

  /** `last` is true for the one part that closes the segment: the final cut's
   *  last slice, and only it. A segment whose tail already left as a full part
   *  sends no marked part at all, which the server accepts, because every part
   *  it holds is then at least the floor. */
  private queuePart(segment: Segment, part: Blob, last: boolean): void {
    segment.partsQueued += 1;
    const partNumber = segment.partsQueued;
    this.pendingBytes += part.size;
    this.enqueue(
      async () => {
        const segmentId = await this.segmentIdFor(segment);
        await this.request(() =>
          this.options.transport.uploadPart(segmentId, partNumber, part, last)
        );
      },
      () => {
        this.pendingBytes -= part.size;
      }
    );
  }

  private close(segment: Segment): void {
    if (segment.closed) return;
    segment.closed = true;
    if (this.segment === segment) this.segment = null;
    if (segment.recorder) {
      segment.recorder.ondataavailable = null;
      segment.recorder.onstop = null;
    }
    segment.tracks.forEach((track) => track.stop());
    if (!this.failed) {
      this.cut(segment, true);
      if (segment.partsQueued > 0) {
        this.enqueue(async () => {
          if (segment.segmentId === null) return;
          const segmentId = segment.segmentId;
          await this.request(() => this.options.transport.completeSegment(segmentId));
          this.completedSegments += 1;
        });
      }
    }
    segment.buffer = [];
    segment.buffered = 0;
    segment.resolveClosed();
  }

  private async segmentIdFor(segment: Segment): Promise<string> {
    if (segment.segmentId !== null) return segment.segmentId;
    const sourceFormat = this.mimeType ?? "";
    const opened = await this.request(() => this.options.transport.openSegment(sourceFormat));
    segment.segmentId = opened.segment_id;
    return opened.segment_id;
  }

  /** Serial: every network call waits for the one before it, which is what
   *  keeps parts and segments in the order they were recorded. */
  private enqueue(operation: () => Promise<void>, settle?: () => void): void {
    this.chain = this.chain.then(async () => {
      try {
        if (!this.failed) await operation();
      } catch (error) {
        this.fail(describe(error, "The assessment recording could not be uploaded."));
      } finally {
        settle?.();
      }
    });
  }

  private async request<T>(call: () => Promise<T>): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await call();
      } catch (error) {
        if (!isRetryable(error) || attempt >= RETRY_DELAYS_MS.length) throw error;
        await this.sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
  }

  private checkBacklog(): void {
    if (this.failed) return;
    if (this.heldBytes() > MAX_PENDING_PARTS * this.options.recording.part_max_bytes) {
      this.fail(
        "The recording could not reach the server fast enough, so it was stopped. " +
          "Your answers are unaffected."
      );
    }
  }

  /** Stop capturing for good and let go of everything held. */
  private fail(reason: string): void {
    if (this.failed) return;
    this.failed = true;
    this.options.onProblem?.(reason);
    const segment = this.segment;
    this.segment = null;
    if (segment !== null) {
      segment.closing = true;
      if (segment.recorder && segment.recorder.state !== "inactive") {
        segment.recorder.ondataavailable = null;
        segment.recorder.stop();
      }
      this.close(segment);
    }
  }

  private async finish(): Promise<void> {
    if (this.completedSegments === 0) {
      // Nothing reached the server whole. A failure has already said why; a
      // session that captured nothing says so here.
      if (!this.failed) {
        this.options.onProblem?.("No assessment recording was captured for this session.");
      }
      return;
    }
    try {
      await this.request(() => this.options.transport.finalize());
    } catch (error) {
      // The bytes ARE stored; only the "please process it" call failed, and
      // the server's own sweep is what picks that up. Reported separately from
      // an upload failure because they are different facts, and without the
      // transport's own wording, which would read as the upload having failed.
      void error;
      this.options.onProblem?.(
        "The assessment recording was uploaded but could not be submitted for processing."
      );
    }
  }
}

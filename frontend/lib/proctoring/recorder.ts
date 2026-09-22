/**
 * The proctored session's recording: capture, upload, finalize.
 *
 * Provenance: the owner ruling of 2026-09-22, which reversed principle P1 and
 * required that the assessment video be compressed and stored securely in S3,
 * linked to the candidate assessment. This file is the browser half of that.
 *
 * WHAT THIS IS NOT. It is not a second media pipeline. The bytes go to the
 * SAME upload and finalize routes the video interview already uses, land on
 * the SAME `video_recordings` row, and are compressed, verified, stored,
 * served and deleted by the same server code. The only thing that differs is
 * the row's `kind`, which the server set when it opened the recording.
 *
 * IT RECORDS THE CAMERA AND THE MICROPHONE, AND NOTHING ELSE. Not the screen:
 * a screen capture would carry whatever else is on the candidate's display,
 * which is their own data and nobody's business here, and the optional
 * screen-capture consent that used to exist was deleted in 2026-09-02 for
 * exactly that reason. Nothing about that changed.
 *
 * THE CHUNKS STAY IN MEMORY UNTIL THE END. `MediaRecorder` hands back blobs as
 * it goes and they are held in an array, then assembled once and posted. That
 * is what the video interview already does, so both kinds hit the same
 * ceiling (`max_upload_bytes`) in the same way, and a candidate is told the
 * ceiling before the recorder opens rather than discovering it at upload.
 *
 * A FAILURE HERE NEVER ENDS AN ASSESSMENT. If the browser cannot open a
 * recorder, or the upload is refused, the candidate keeps answering and the
 * server's recording row keeps the honest status the hiring team reads. The
 * alternative would be a monitoring feature that can fail a candidate's
 * interview, which is a much worse outcome than a missing recording, and the
 * dashboard already has a word for a recording that did not arrive.
 */

/** What the server returned when it opened the recording. */
export interface SessionMediaStart {
  conversation_id: string;
  recording_id: string;
  status: string;
  max_upload_bytes: number;
  max_duration_seconds: number;
}

export interface RecorderOptions {
  /** The two streams the monitoring session already holds open. */
  camera: MediaStream;
  microphone: MediaStream;
  recording: SessionMediaStart;
  /** Posts the assembled blob. Injected so a test drives it without a network. */
  upload: (conversationId: string, blob: Blob) => Promise<void>;
  /** Tells the server the bytes are all there and the pipeline may run. */
  finalize: (conversationId: string) => Promise<void>;
  /** Reported, never thrown: see the header. */
  onProblem?: (reason: string) => void;
  recorderFactory?: (stream: MediaStream, options: MediaRecorderOptions) => MediaRecorder;
  /** The container probe, injected for the same reason `recorderFactory` is:
   *  `MediaRecorder` is a browser global and a test environment has none, so
   *  without this seam every test here would exercise the
   *  cannot-record branch and prove nothing about the five decisions below. */
  isTypeSupported?: (type: string) => boolean;
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
 * How often `MediaRecorder` hands back a blob. Short enough that a crashed tab
 * loses seconds rather than the whole session's tail, long enough that the
 * array does not become thousands of entries in an hour.
 */
const TIMESLICE_MS = 10_000;

export class SessionRecorder {
  private recorder: MediaRecorder | null = null;
  private readonly chunks: Blob[] = [];
  private stopped = false;
  /** Resolves once the upload has been attempted, so a caller that wants to
   *  wait for the recording to be handed over can. `stop()` itself does not
   *  block: the session is ending and the UI must not sit on a network call. */
  private settled: Promise<void> = Promise.resolve();

  constructor(private readonly options: RecorderOptions) {}

  start(): void {
    const mimeType = this.options.isTypeSupported
      ? pickMimeType(this.options.isTypeSupported)
      : pickMimeType();
    if (mimeType === null) {
      this.options.onProblem?.(
        "This browser cannot record the assessment, so no recording was made."
      );
      return;
    }
    const combined = new MediaStream([
      ...this.options.camera.getVideoTracks(),
      ...this.options.microphone.getAudioTracks(),
    ]);
    const factory =
      this.options.recorderFactory ??
      ((stream: MediaStream, opts: MediaRecorderOptions) =>
        new MediaRecorder(stream, opts));
    let recorder: MediaRecorder;
    try {
      recorder = factory(combined, { mimeType });
    } catch (error) {
      this.options.onProblem?.(
        error instanceof Error
          ? error.message
          : "The assessment recorder could not be opened."
      );
      return;
    }
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    };
    recorder.onstop = () => {
      this.settled = this.deliver(mimeType);
    };
    recorder.onerror = () => {
      this.options.onProblem?.(
        "The assessment recording stopped unexpectedly. Your answers are unaffected."
      );
    };
    this.recorder = recorder;
    recorder.start(TIMESLICE_MS);
  }

  /**
   * Stop recording and hand the bytes over. Returns immediately; `finished()`
   * is how a caller waits. Called BEFORE the session releases its tracks,
   * because a recorder whose tracks have already ended produces nothing.
   */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    const recorder = this.recorder;
    if (recorder === null || recorder.state === "inactive") return;
    recorder.stop();
  }

  /** Resolves once the upload and finalize have been attempted. */
  finished(): Promise<void> {
    return this.settled;
  }

  private async deliver(mimeType: string): Promise<void> {
    if (this.chunks.length === 0) {
      this.options.onProblem?.(
        "No assessment recording was captured for this session."
      );
      return;
    }
    const blob = new Blob(this.chunks, { type: mimeType });
    // Emptied before the network call, not after: the array is the only thing
    // holding the recording on this thread, and a candidate's media must not
    // stay resident while a retry is decided somewhere else.
    this.chunks.length = 0;
    if (blob.size > this.options.recording.max_upload_bytes) {
      this.options.onProblem?.(
        "The assessment recording was longer than this assessment accepts, so " +
          "it was not uploaded."
      );
      return;
    }
    const conversationId = this.options.recording.conversation_id;
    try {
      await this.options.upload(conversationId, blob);
    } catch (error) {
      this.options.onProblem?.(
        error instanceof Error
          ? error.message
          : "The assessment recording could not be uploaded."
      );
      return;
    }
    try {
      await this.options.finalize(conversationId);
    } catch (error) {
      // The bytes ARE stored; only the "please process it" call failed, and
      // the server's own reconciliation is what picks that up. Reported
      // separately from an upload failure because they are different facts.
      //
      // THE PROVIDER'S OWN MESSAGE IS DELIBERATELY NOT USED HERE, unlike the
      // upload branch above. A raw "gateway timeout" is indistinguishable
      // from the upload having failed, which is the one thing this branch
      // exists to tell the candidate apart, and it would also be the only
      // place in this flow where a transport's wording reaches them. What
      // went wrong belongs in the log; what it MEANS for them is that their
      // recording is safe and nothing is being asked of them.
      void error;
      this.options.onProblem?.(
        "The assessment recording was uploaded but could not be submitted "
        + "for processing."
      );
    }
  }
}

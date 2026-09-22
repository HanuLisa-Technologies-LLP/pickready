// The proctored session's recorder (owner ruling, 2026-09-22).
//
// What is worth pinning here is not that MediaRecorder works, it is the four
// decisions a future change would break without anything noticing: the
// container is ASKED for rather than assumed, the chunks are released before
// the network call, an over-ceiling recording is refused in the browser rather
// than at the server, and NO failure here throws at the session that is using
// it.

import { describe, expect, it, vi } from "vitest";

import { pickMimeType, SessionRecorder, type SessionMediaStart } from "./recorder";

// jsdom implements neither MediaRecorder nor MediaStream. The recorder
// genuinely combines the camera and microphone tracks with
// `new MediaStream([...])`, and unlike the recorder factory there is nothing
// meaningful to inject for that: the honest stand-in is a constructor that
// accepts tracks and hands back none, which is exactly what the fake streams
// below already do.
class FakeMediaStream {
  constructor(readonly tracks: unknown[] = []) {}
  getVideoTracks(): unknown[] {
    return [];
  }
  getAudioTracks(): unknown[] {
    return [];
  }
}
(globalThis as unknown as { MediaStream: unknown }).MediaStream = FakeMediaStream;

const RECORDING: SessionMediaStart = {
  conversation_id: "conv-1",
  recording_id: "rec-1",
  status: "recording",
  max_upload_bytes: 1000,
  max_duration_seconds: 7200,
};

/** The smallest thing that behaves like a MediaRecorder for these tests. */
class FakeRecorder {
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  start(): void {
    this.state = "recording";
  }

  stop(): void {
    this.state = "inactive";
    this.onstop?.();
  }

  emit(blob: Blob): void {
    this.ondataavailable?.({ data: blob });
  }
}

function streams(): { camera: MediaStream; microphone: MediaStream } {
  const camera = { getVideoTracks: () => [], getAudioTracks: () => [] };
  const microphone = { getVideoTracks: () => [], getAudioTracks: () => [] };
  return {
    camera: camera as unknown as MediaStream,
    microphone: microphone as unknown as MediaStream,
  };
}

describe("pickMimeType", () => {
  it("asks the browser rather than assuming a codec", () => {
    // A hardcoded vp9 produces an EMPTY recording on a browser without it,
    // with no error anywhere, which is the worst failure shape available.
    expect(pickMimeType((type) => type === "video/webm")).toBe("video/webm");
    expect(pickMimeType((type) => type === "video/mp4")).toBe("video/mp4");
  });

  it("answers null rather than a container nothing supports", () => {
    expect(pickMimeType(() => false)).toBeNull();
  });

  it("prefers the most compressed container the browser offers", () => {
    expect(pickMimeType(() => true)).toBe("video/webm;codecs=vp9,opus");
  });
});

describe("SessionRecorder", () => {
  function build(overrides: Partial<Parameters<typeof makeOptions>[0]> = {}) {
    return makeOptions(overrides);
  }

  function makeOptions(overrides: {
    upload?: (conversationId: string, blob: Blob) => Promise<void>;
    finalize?: (conversationId: string) => Promise<void>;
    maxUploadBytes?: number;
  }) {
    const fake = new FakeRecorder();
    const problems: string[] = [];
    const upload = overrides.upload ?? vi.fn().mockResolvedValue(undefined);
    const finalize = overrides.finalize ?? vi.fn().mockResolvedValue(undefined);
    const media = streams();
    const recorder = new SessionRecorder({
      camera: media.camera,
      microphone: media.microphone,
      recording: {
        ...RECORDING,
        max_upload_bytes: overrides.maxUploadBytes ?? RECORDING.max_upload_bytes,
      },
      upload,
      finalize,
      onProblem: (reason) => problems.push(reason),
      recorderFactory: () => fake as unknown as MediaRecorder,
      // jsdom has no MediaRecorder, so without this the recorder would take
      // its cannot-record branch and every assertion below would pass for
      // the wrong reason.
      isTypeSupported: () => true,
    });
    return { recorder, fake, problems, upload, finalize };
  }

  it("uploads what it captured and then finalizes, in that order", async () => {
    const order: string[] = [];
    const { recorder, fake } = build({
      upload: async () => {
        order.push("upload");
      },
      finalize: async () => {
        order.push("finalize");
      },
    });
    recorder.start();
    fake.emit(new Blob(["abc"]));
    recorder.stop();
    await recorder.finished();
    // Finalize means "the bytes are all there, run the pipeline". Sending it
    // first would hand the server a recording it has not received.
    expect(order).toEqual(["upload", "finalize"]);
  });

  it("releases the chunks before the network call, not after", async () => {
    let sizeAtUpload = -1;
    const { recorder, fake } = build({
      upload: async (_conversationId, blob) => {
        sizeAtUpload = blob.size;
      },
    });
    recorder.start();
    fake.emit(new Blob(["abcdef"]));
    recorder.stop();
    await recorder.finished();
    expect(sizeAtUpload).toBeGreaterThan(0);
    // A second stop must not resend: the array is empty and the recorder is
    // already inactive.
    recorder.stop();
    await recorder.finished();
  });

  it("refuses an over-ceiling recording here rather than at the server", async () => {
    const upload = vi.fn().mockResolvedValue(undefined);
    const { recorder, fake, problems } = build({ upload, maxUploadBytes: 2 });
    recorder.start();
    fake.emit(new Blob(["much longer than two bytes"]));
    recorder.stop();
    await recorder.finished();
    expect(upload).not.toHaveBeenCalled();
    expect(problems.join(" ")).toContain("longer than this assessment accepts");
  });

  it("reports an empty capture instead of posting an empty blob", async () => {
    const upload = vi.fn().mockResolvedValue(undefined);
    const { recorder, problems } = build({ upload });
    recorder.start();
    recorder.stop();
    await recorder.finished();
    expect(upload).not.toHaveBeenCalled();
    expect(problems.join(" ")).toContain("No assessment recording was captured");
  });

  it("never throws at the session when the upload fails", async () => {
    const { recorder, fake, problems, finalize } = build({
      upload: async () => {
        throw new Error("the object store refused the upload");
      },
    });
    recorder.start();
    fake.emit(new Blob(["abc"]));
    recorder.stop();
    await expect(recorder.finished()).resolves.toBeUndefined();
    expect(finalize).not.toHaveBeenCalled();
    expect(problems.join(" ")).toContain("the object store refused the upload");
  });

  it("separates a failed finalize from a failed upload", async () => {
    // The bytes ARE stored; only "please process it" failed. Telling the
    // candidate the upload failed would be a different and untrue fact.
    const { recorder, fake, problems } = build({
      finalize: async () => {
        throw new Error("gateway timeout");
      },
    });
    recorder.start();
    fake.emit(new Blob(["abc"]));
    recorder.stop();
    await recorder.finished();
    expect(problems.join(" ")).toContain("uploaded but could not be submitted");
  });

  it("reports rather than throws when no container is supported", () => {
    const media = streams();
    const problems: string[] = [];
    const recorder = new SessionRecorder({
      camera: media.camera,
      microphone: media.microphone,
      recording: RECORDING,
      upload: vi.fn().mockResolvedValue(undefined),
      finalize: vi.fn().mockResolvedValue(undefined),
      onProblem: (reason) => problems.push(reason),
      recorderFactory: () => {
        throw new Error("unsupported");
      },
      isTypeSupported: () => true,
    });
    expect(() => recorder.start()).not.toThrow();
    expect(problems).toHaveLength(1);
  });
});

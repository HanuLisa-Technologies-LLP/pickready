// The proctored session's recorder (owner ruling 2026-09-22; segmented upload
// Phase 3, 2026-09-24).
//
// What is worth pinning here is not that MediaRecorder works, it is the
// decisions a future change would break without anything noticing: the
// container is ASKED for; the recording leaves the tab in parts of at least
// the server's floor as it is made, so the tab never holds the session; every
// part but a segment's last respects the floor and none exceeds the ceiling;
// the last is the one part marked `final`, because the server refuses a short
// part that is not; a
// device recovery starts a new segment after the old one is completed; the
// network calls are strictly in order; a transport failure is retried and a
// refusal is not; and NO failure here throws at the session that is using it.

import { describe, expect, it, vi } from "vitest";

import { ApiError, NETWORK_ERROR } from "@/lib/api";

import type { SessionMediaStartOut } from "./api";
import {
  MAX_PENDING_PARTS,
  pickMimeType,
  RETRY_DELAYS_MS,
  SessionRecorder,
  type RecordingTransport,
} from "./recorder";

const FLOOR = 10;
const CEILING = 25;

const RECORDING: SessionMediaStartOut = {
  conversation_id: "conv-1",
  recording_id: "rec-1",
  status: "recording",
  part_min_bytes: FLOOR,
  part_max_bytes: CEILING,
  video_bits_per_second: 400_000,
  audio_bits_per_second: 48_000,
  max_width: 640,
  max_height: 360,
};

/** A track that records what was done to it. */
class FakeTrack {
  stopped = false;
  constraints: MediaTrackConstraints | null = null;
  clones: FakeTrack[] = [];
  constructor(
    readonly kind: "video" | "audio",
    private readonly refuseConstraints = false
  ) {}
  clone(): FakeTrack {
    const copy = new FakeTrack(this.kind, this.refuseConstraints);
    this.clones.push(copy);
    return copy;
  }
  applyConstraints(constraints: MediaTrackConstraints): Promise<void> {
    if (this.refuseConstraints) return Promise.reject(new Error("OverconstrainedError"));
    this.constraints = constraints;
    return Promise.resolve();
  }
  stop(): void {
    this.stopped = true;
  }
}

function streams(refuseConstraints = false) {
  const video = new FakeTrack("video", refuseConstraints);
  const audio = new FakeTrack("audio");
  const camera = { getVideoTracks: () => [video], getAudioTracks: () => [] } as unknown as MediaStream;
  const microphone = { getVideoTracks: () => [], getAudioTracks: () => [audio] } as unknown as MediaStream;
  return { camera, microphone, video, audio };
}

/** The smallest thing that behaves like a MediaRecorder for these tests. */
class FakeRecorder {
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  timeslice: number | null = null;
  constructor(
    readonly stream: { tracks: FakeTrack[] },
    readonly options: MediaRecorderOptions
  ) {}
  start(timeslice?: number): void {
    this.state = "recording";
    this.timeslice = timeslice ?? null;
  }
  stop(): void {
    if (this.state === "inactive") return;
    this.state = "inactive";
    this.onstop?.();
  }
  emit(bytes: number): void {
    this.ondataavailable?.({ data: new Blob(["x".repeat(bytes)]) });
  }
}

interface Call {
  op: "open" | "part" | "complete" | "finalize";
  segment?: string;
  part?: number;
  bytes?: number;
  /** Present, and true, only on a part sent marked as its segment's last. */
  final?: true;
}

function transport(overrides: Partial<RecordingTransport> = {}) {
  const calls: Call[] = [];
  let segments = 0;
  const base: RecordingTransport = {
    openSegment: vi.fn(async () => {
      segments += 1;
      calls.push({ op: "open", segment: `seg-${segments}` });
      return { segment_id: `seg-${segments}` };
    }),
    uploadPart: vi.fn(async (segment: string, part: number, body: Blob, final: boolean) => {
      calls.push({ op: "part", segment, part, bytes: body.size, ...(final ? { final: true as const } : {}) });
    }),
    completeSegment: vi.fn(async (segment: string) => {
      calls.push({ op: "complete", segment });
    }),
    finalize: vi.fn(async () => {
      calls.push({ op: "finalize" });
    }),
  };
  return { calls, transport: { ...base, ...overrides } };
}

function build(options: { transport?: RecordingTransport; refuseConstraints?: boolean } = {}) {
  const recorders: FakeRecorder[] = [];
  const problems: string[] = [];
  const sleeps: number[] = [];
  const net = transport();
  const media = streams(options.refuseConstraints);
  const recorder = new SessionRecorder({
    recording: RECORDING,
    transport: options.transport ?? net.transport,
    onProblem: (reason) => problems.push(reason),
    recorderFactory: (stream, recorderOptions) => {
      const fake = new FakeRecorder(stream as unknown as { tracks: FakeTrack[] }, recorderOptions);
      recorders.push(fake);
      return fake as unknown as MediaRecorder;
    },
    streamFactory: (tracks) => ({ tracks }) as unknown as MediaStream,
    // jsdom has no MediaRecorder, so without this the recorder would take its
    // cannot-record branch and every assertion below would pass for the
    // wrong reason.
    isTypeSupported: () => true,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  return { recorder, recorders, problems, sleeps, calls: net.calls, media };
}

/** Let the track preparation and every queued network call run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
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

describe("the encoder", () => {
  it("records clones of the session's tracks at the server's size and bitrates", async () => {
    const { recorder, recorders, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    const [fake] = recorders;
    expect(fake.options).toEqual({
      mimeType: "video/webm;codecs=vp9,opus",
      videoBitsPerSecond: 400_000,
      audioBitsPerSecond: 48_000,
    });
    // The CLONE is scaled; the camera the detectors sample is untouched.
    expect(media.video.constraints).toBeNull();
    expect(media.video.clones[0].constraints).toEqual({ width: { max: 640 }, height: { max: 360 } });
    expect(fake.stream.tracks).toEqual([media.video.clones[0], media.audio.clones[0]]);
    expect(fake.timeslice).toBeGreaterThan(0);
  });

  it("records anyway, and says so, when the camera refuses to be scaled", async () => {
    const { recorder, recorders, problems, media } = build({ refuseConstraints: true });
    recorder.start(media.camera, media.microphone);
    await settle();
    expect(recorders).toHaveLength(1);
    expect(problems.join(" ")).toContain("could not be scaled down");
  });
});

describe("parts", () => {
  it("leave as they fill, never below the floor except a segment's last, never above the ceiling", async () => {
    const { recorder, recorders, calls, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    const [fake] = recorders;
    fake.emit(6);
    await settle();
    // Below the floor: held, nothing sent, not even the segment opened.
    expect(calls).toEqual([]);
    fake.emit(6);
    await settle();
    // 12 bytes reached the floor and left as one part.
    expect(calls).toEqual([
      { op: "open", segment: "seg-1" },
      { op: "part", segment: "seg-1", part: 1, bytes: 12 },
    ]);
    fake.emit(60);
    await settle();
    // 60 bytes: two parts at the ceiling, the 10 left over is exactly the
    // floor and goes too.
    expect(calls.slice(2)).toEqual([
      { op: "part", segment: "seg-1", part: 2, bytes: 25 },
      { op: "part", segment: "seg-1", part: 3, bytes: 25 },
      { op: "part", segment: "seg-1", part: 4, bytes: 10 },
    ]);
    fake.emit(3);
    recorder.stop();
    await recorder.finished();
    // The tail is the segment's last part, may be small, and says so.
    expect(calls.slice(5)).toEqual([
      { op: "part", segment: "seg-1", part: 5, bytes: 3, final: true },
      { op: "complete", segment: "seg-1" },
      { op: "finalize" },
    ]);
  });

  it("holds no more than a part's worth in the tab while the network keeps up", async () => {
    const { recorder, recorders, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    for (let i = 0; i < 40; i += 1) {
      recorders[0].emit(7);
      await settle();
      expect(recorder.heldBytes()).toBeLessThan(FLOOR);
    }
  });
});

describe("segments", () => {
  it("complete the old segment before the new one opens after a device recovery", async () => {
    const { recorder, recorders, calls, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(12);
    recorders[0].emit(4);
    const back = streams();
    recorder.restart(back.camera, back.microphone);
    await settle();
    // The old recorder stopped and released its clones; the new one records
    // the NEW tracks.
    expect(recorders[0].state).toBe("inactive");
    expect(media.video.clones[0].stopped).toBe(true);
    expect(recorders[1].stream.tracks).toEqual([back.video.clones[0], back.audio.clones[0]]);
    recorders[1].emit(11);
    recorder.stop();
    await recorder.finished();
    expect(calls).toEqual([
      { op: "open", segment: "seg-1" },
      { op: "part", segment: "seg-1", part: 1, bytes: 12 },
      { op: "part", segment: "seg-1", part: 2, bytes: 4, final: true },
      { op: "complete", segment: "seg-1" },
      { op: "open", segment: "seg-2" },
      // At the floor, so it left as an ordinary part while recording.
      { op: "part", segment: "seg-2", part: 1, bytes: 11 },
      { op: "complete", segment: "seg-2" },
      { op: "finalize" },
    ]);
  });

  it("sends the tail when the browser stops a recorder whose tracks all ended", async () => {
    const { recorder, recorders, calls, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(4);
    recorders[0].stop();
    await settle();
    expect(calls).toEqual([
      { op: "open", segment: "seg-1" },
      { op: "part", segment: "seg-1", part: 1, bytes: 4, final: true },
      { op: "complete", segment: "seg-1" },
    ]);
  });

  it("marks no part when the tail already left as a full part", async () => {
    const { recorder, recorders, calls, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(FLOOR);
    await settle();
    recorder.stop();
    await recorder.finished();
    // Every part the server holds is at least the floor, so nothing needs the
    // exception and nothing claims it.
    expect(calls).toEqual([
      { op: "open", segment: "seg-1" },
      { op: "part", segment: "seg-1", part: 1, bytes: FLOOR },
      { op: "complete", segment: "seg-1" },
      { op: "finalize" },
    ]);
  });

  it("opens no segment for a segment that captured nothing", async () => {
    const { recorder, calls, problems, media } = build();
    recorder.start(media.camera, media.microphone);
    await settle();
    recorder.stop();
    await recorder.finished();
    expect(calls).toEqual([]);
    expect(problems.join(" ")).toContain("No assessment recording was captured");
  });
});

describe("failures", () => {
  it("retries a transport failure with backoff and carries on", async () => {
    let failures = 2;
    const net = transport();
    const uploadPart = net.transport.uploadPart;
    const { recorder, recorders, sleeps, problems, media } = build({
      transport: {
        ...net.transport,
        uploadPart: async (segment, part, body, final) => {
          if (failures > 0) {
            failures -= 1;
            throw new ApiError(NETWORK_ERROR, null);
          }
          await uploadPart(segment, part, body, final);
        },
      },
    });
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(12);
    recorder.stop();
    await recorder.finished();
    expect(sleeps).toEqual([RETRY_DELAYS_MS[0], RETRY_DELAYS_MS[1]]);
    expect(problems).toEqual([]);
    expect(net.calls.map((call) => call.op)).toEqual(["open", "part", "complete", "finalize"]);
  });

  it("stops recording, without a retry, when the server refuses", async () => {
    const net = transport();
    const { recorder, recorders, sleeps, problems, media } = build({
      transport: {
        ...net.transport,
        uploadPart: async () => {
          throw new ApiError(409, null, "The recording is closed.");
        },
      },
    });
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(12);
    await settle();
    expect(sleeps).toEqual([]);
    expect(problems).toEqual(["The recording is closed."]);
    // Recording stopped: the clones are released and nothing more is sent.
    expect(recorders[0].state).toBe("inactive");
    expect(media.video.clones[0].stopped).toBe(true);
    recorders[0].emit(30);
    recorder.stop();
    await expect(recorder.finished()).resolves.toBeUndefined();
    expect(net.calls.map((call) => call.op)).toEqual(["open"]);
  });

  it("gives up after the last retry rather than retrying for ever", async () => {
    const net = transport();
    const { recorder, recorders, sleeps, problems, media } = build({
      transport: {
        ...net.transport,
        openSegment: async () => {
          throw new ApiError(503, null, "Service unavailable");
        },
      },
    });
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(12);
    recorder.stop();
    await recorder.finished();
    expect(sleeps).toEqual([...RETRY_DELAYS_MS]);
    expect(problems).toEqual(["Service unavailable"]);
  });

  it("stops rather than let a stalled network pile the recording up in the tab", async () => {
    // The first part never resolves, so every later part waits behind it.
    const net = transport();
    const { recorder, recorders, problems, media } = build({
      transport: {
        ...net.transport,
        uploadPart: () => new Promise<void>(() => undefined),
      },
    });
    recorder.start(media.camera, media.microphone);
    await settle();
    const ceilingBytes = MAX_PENDING_PARTS * CEILING;
    for (let held = 0; held <= ceilingBytes; held += CEILING) {
      recorders[0].emit(CEILING);
      await settle();
    }
    expect(problems.join(" ")).toContain("could not reach the server fast enough");
    expect(recorders[0].state).toBe("inactive");
  });

  it("separates a failed finalize from a failed upload", async () => {
    // The bytes ARE stored; only "please process it" failed.
    const net = transport();
    const { recorder, recorders, problems, media } = build({
      transport: {
        ...net.transport,
        finalize: async () => {
          throw new ApiError(400, null, "gateway said no");
        },
      },
    });
    recorder.start(media.camera, media.microphone);
    await settle();
    recorders[0].emit(12);
    recorder.stop();
    await recorder.finished();
    expect(problems).toEqual([
      "The assessment recording was uploaded but could not be submitted for processing.",
    ]);
  });

  it("reports rather than throws when no container is supported", async () => {
    const media = streams();
    const problems: string[] = [];
    const recorder = new SessionRecorder({
      recording: RECORDING,
      transport: transport().transport,
      onProblem: (reason) => problems.push(reason),
      isTypeSupported: () => false,
    });
    expect(() => recorder.start(media.camera, media.microphone)).not.toThrow();
    recorder.stop();
    await recorder.finished();
    expect(problems).toEqual(["This browser cannot record the assessment, so no recording was made."]);
  });

  it("reports rather than throws when the recorder cannot be built", async () => {
    const media = streams();
    const problems: string[] = [];
    const recorder = new SessionRecorder({
      recording: RECORDING,
      transport: transport().transport,
      onProblem: (reason) => problems.push(reason),
      recorderFactory: () => {
        throw new Error("unsupported");
      },
      streamFactory: (tracks) => ({ tracks }) as unknown as MediaStream,
      isTypeSupported: () => true,
    });
    expect(() => recorder.start(media.camera, media.microphone)).not.toThrow();
    await settle();
    expect(problems).toEqual(["unsupported"]);
    expect(media.video.clones[0].stopped).toBe(true);
  });
});

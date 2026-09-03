// The camera rules, frame by frame.
//
// Every threshold comes from the config object the server sent, so the
// fixture below is the only place numbers appear and no rule carries one of
// its own. What is being defended: the debounce (a single frame must never
// produce an event), the difference between a face that is absent and a lens
// that is covered, and the rule that one bad identity reading is never an
// identity failure.

import { describe, expect, it } from "vitest";

import type { ProctoringClientConfig } from "./config";
import { DetectionRules, type FrameDetections } from "./detections";

const CONFIG = {
  object_confidence_threshold: 0.65,
  object_consecutive_frames: 3,
  obstruction_seconds: 60,
  obstruction_variance_threshold: 12,
  face_absent_moderate_seconds: 20,
  face_absent_extended_seconds: 90,
  low_light_luminance_threshold: 40,
  face_distance_threshold: 0.6,
} as unknown as ProctoringClientConfig;

function frame(at: number, overrides: Partial<FrameDetections> = {}): FrameDetections {
  return {
    at,
    objects: [],
    faces: 1,
    persons: 1,
    luminance: 120,
    variance: 45,
    ...overrides,
  };
}

const phone = { label: "cell phone", score: 0.9 };

function types(rules: DetectionRules, input: FrameDetections): string[] {
  return rules.observe(input).events.map((event) => event.event_type);
}

describe("object rules", () => {
  it("never reports an object seen in fewer than the required consecutive frames", () => {
    const rules = new DetectionRules(CONFIG);
    expect(types(rules, frame(0, { objects: [phone] }))).toEqual([]);
    expect(types(rules, frame(500, { objects: [phone] }))).toEqual([]);
    // A gap resets the run: motion blur and a real phone look the same for
    // one frame, and only persistence tells them apart.
    expect(types(rules, frame(1000))).toEqual([]);
    expect(types(rules, frame(1500, { objects: [phone] }))).toEqual([]);
    expect(types(rules, frame(2000, { objects: [phone] }))).toEqual([]);
    expect(types(rules, frame(2500, { objects: [phone] }))).toEqual(["DEVICE_DETECTED_PHONE"]);
  });

  it("says it is confirming while a run is building, so the sampler speeds up", () => {
    const rules = new DetectionRules(CONFIG);
    expect(rules.observe(frame(0, { objects: [phone] })).confirming).toBe(true);
    expect(rules.observe(frame(500)).confirming).toBe(false);
  });

  it("discards a detection under the confidence floor", () => {
    const rules = new DetectionRules(CONFIG);
    const weak = { label: "cell phone", score: 0.4 };
    for (const at of [0, 500, 1000, 1500]) {
      expect(types(rules, frame(at, { objects: [weak] }))).toEqual([]);
    }
  });

  it("reports a phone once while it stays on the desk, and again after it leaves and returns", () => {
    const rules = new DetectionRules(CONFIG);
    for (const at of [0, 500, 1000, 1500, 2000]) types(rules, frame(at, { objects: [phone] }));
    // Frames three onward are the same presence, so no second request.
    expect(types(rules, frame(2500, { objects: [phone] }))).toEqual([]);
    types(rules, frame(3000));
    for (const at of [3500, 4000]) expect(types(rules, frame(at, { objects: [phone] }))).toEqual([]);
    expect(types(rules, frame(4500, { objects: [phone] }))).toEqual(["DEVICE_DETECTED_PHONE"]);
  });

  it("reports a second person from either the face count or the person count", () => {
    const byFaces = new DetectionRules(CONFIG);
    for (const at of [0, 500]) types(byFaces, frame(at, { faces: 2, persons: 1 }));
    expect(types(byFaces, frame(1000, { faces: 2, persons: 1 }))).toEqual(["SECOND_PERSON_DETECTED"]);

    const byPersons = new DetectionRules(CONFIG);
    for (const at of [0, 500]) types(byPersons, frame(at, { persons: 2 }));
    expect(types(byPersons, frame(1000, { persons: 2 }))).toEqual(["SECOND_PERSON_DETECTED"]);
  });

  it("ignores a class the catalog has no event for", () => {
    const rules = new DetectionRules(CONFIG);
    const book = { label: "book", score: 0.99 };
    for (const at of [0, 500, 1000, 1500]) {
      expect(types(rules, frame(at, { objects: [book] }))).toEqual([]);
    }
  });
});

describe("face absence and obstruction", () => {
  it("reports a brief absence when it ends, with how long it lasted", () => {
    const rules = new DetectionRules(CONFIG);
    types(rules, frame(0));
    types(rules, frame(1000, { faces: 0, persons: 0 }));
    types(rules, frame(5000, { faces: 0, persons: 0 }));
    const events = rules.observe(frame(9000)).events;
    expect(events.map((event) => event.event_type)).toEqual(["FACE_ABSENT_BRIEF"]);
    expect(events[0].duration_ms).toBe(8000);
  });

  it("reports the moderate threshold as it is crossed, not when the candidate returns", () => {
    const rules = new DetectionRules(CONFIG);
    types(rules, frame(1000, { faces: 0, persons: 0 }));
    expect(types(rules, frame(20_000, { faces: 0, persons: 0 }))).toEqual([]);
    const events = rules.observe(frame(21_500, { faces: 0, persons: 0 })).events;
    expect(events.map((event) => event.event_type)).toEqual(["FACE_ABSENT_MODERATE"]);
    expect(events[0].duration_ms).toBe(20_500);
    // Already reported: the return adds nothing, and no brief follows a
    // moderate for the same absence.
    expect(types(rules, frame(30_000))).toEqual([]);
  });

  it("escalates to the extended threshold in the same absence", () => {
    const rules = new DetectionRules(CONFIG);
    types(rules, frame(0, { faces: 0, persons: 0 }));
    expect(types(rules, frame(25_000, { faces: 0, persons: 0 }))).toEqual(["FACE_ABSENT_MODERATE"]);
    expect(types(rules, frame(95_000, { faces: 0, persons: 0 }))).toEqual(["FACE_ABSENT_EXTENDED"]);
  });

  it("calls a near-uniform frame with no face an obstruction, on its own clock", () => {
    // A covered lens is deliberate, so it reaches termination faster than an
    // absence does; the two are timed separately from the same frames.
    const rules = new DetectionRules(CONFIG);
    const covered = { faces: 0, persons: 0, variance: 2, luminance: 60 };
    types(rules, frame(0, covered));
    expect(types(rules, frame(30_000, covered))).toEqual(["FACE_ABSENT_MODERATE"]);
    const events = rules.observe(frame(61_000, covered)).events;
    expect(events.map((event) => event.event_type)).toEqual(["CAMERA_OBSTRUCTED"]);
    expect(events[0].duration_ms).toBe(61_000);
  });

  it("does not call a dark room with a visible face an obstruction", () => {
    const rules = new DetectionRules(CONFIG);
    const events = rules.observe(frame(0, { luminance: 20, variance: 30 })).events;
    expect(events.map((event) => event.event_type)).toEqual(["LOW_LIGHT"]);
    // Once per dark episode, not once per frame.
    expect(types(rules, frame(1000, { luminance: 20, variance: 30 }))).toEqual([]);
  });
});

describe("identity", () => {
  it("reports one mismatch as one check and leaves the counting to the server", () => {
    const rules = new DetectionRules(CONFIG);
    expect(rules.observeIdentity(0.75).map((event) => event.event_type)).toEqual([
      "IDENTITY_CHECK_MISMATCH",
    ]);
    expect(rules.identityMatched).toBe(false);
  });

  it("reports nothing on a match, and remembers it for the heartbeat", () => {
    const rules = new DetectionRules(CONFIG);
    expect(rules.observeIdentity(0.3)).toEqual([]);
    expect(rules.identityMatched).toBe(true);
  });

  it("treats a check with no measurable face as no answer, never as a mismatch", () => {
    const rules = new DetectionRules(CONFIG);
    expect(rules.observeIdentity(null)).toEqual([]);
    expect(rules.identityMatched).toBeNull();
  });
});

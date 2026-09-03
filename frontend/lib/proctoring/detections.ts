/**
 * The camera rules (proctoring spec 3.1, 3.3, 4.1, 4.2, 4.3, 4.6), as pure
 * arithmetic over what the worker reports.
 *
 * The worker posts per-frame DETECTIONS: labelled objects with their scores,
 * a face count, a person count, and two numbers about the frame itself, its
 * mean luminance and the standard deviation of its grey levels. This module
 * turns a sequence of those into EVENT REQUESTS and never sees a pixel. It is
 * deliberately free of timers and media so `detections.test.ts` can drive it
 * frame by frame with a fake clock.
 *
 * DEBOUNCE IS MANDATORY (spec 3.1). An object or a second person must be seen
 * in `object_consecutive_frames` consecutive frames before an event is
 * requested, and it is requested once per continuous presence: it is not
 * repeated while the phone stays on the desk, and it is requested again only
 * after the phone has gone and come back. The server's cooldown is the second
 * guard; this one keeps the request count honest.
 *
 * ABSENCE AND OBSTRUCTION ARE DIFFERENT THINGS (spec 4.6). No face in a frame
 * with normal variance is a candidate who looked away; no face in a
 * near-uniform frame is a covered lens. Both are timed here, in the browser,
 * because the browser is the only place that sees consecutive frames; the
 * server checks the reported duration against its own thresholds and
 * downgrades a report that fell short. Brief absences are reported when they
 * END, with their length; the moderate and extended thresholds are reported
 * when they are CROSSED, so a candidate who has been out of view for ninety
 * seconds is not waiting on their own return for the session to end.
 *
 * A single mismatch is never an identity failure (spec 3.3). It is reported
 * as one check; the server counts the second consecutive one.
 */
import { OBJECT_EVENT_FOR_LABEL, type ClientEventType } from "./catalog";
import type { ProctoringClientConfig } from "./config";
import { seconds } from "./config";
import type { EventDraft } from "./events";

export interface ObjectDetection {
  label: string;
  score: number;
}

export interface FrameDetections {
  /** When the frame was captured, on the caller's clock. */
  at: number;
  objects: ObjectDetection[];
  faces: number;
  persons: number;
  luminance: number;
  variance: number;
}

export interface RuleOutcome {
  events: EventDraft[];
  /** Something MAY be present: sample faster until it is confirmed or gone. */
  confirming: boolean;
}

interface Run {
  count: number;
  minScore: number;
  reported: boolean;
}

type RuleConfig = Pick<
  ProctoringClientConfig,
  | "object_confidence_threshold"
  | "object_consecutive_frames"
  | "obstruction_seconds"
  | "obstruction_variance_threshold"
  | "face_absent_moderate_seconds"
  | "face_absent_extended_seconds"
  | "low_light_luminance_threshold"
  | "face_distance_threshold"
>;

const SECOND_PERSON = "SECOND_PERSON_DETECTED";

export class DetectionRules {
  private readonly runs = new Map<ClientEventType, Run>();
  private absentSince: number | null = null;
  private obstructedSince: number | null = null;
  private absenceNearUniform = false;
  private moderateReported = false;
  private extendedReported = false;
  private obstructionReported = false;
  private lowLight = false;
  private lastIdentity: boolean | null = null;

  constructor(private readonly config: RuleConfig) {}

  /** The most recent identity comparison, for the heartbeat. */
  get identityMatched(): boolean | null {
    return this.lastIdentity;
  }

  observe(frame: FrameDetections): RuleOutcome {
    const events: EventDraft[] = [];
    let confirming = false;

    // Objects the specification says to act on, above the confidence floor.
    const present = new Map<ClientEventType, number>();
    for (const detection of frame.objects) {
      const eventType = OBJECT_EVENT_FOR_LABEL[detection.label];
      if (!eventType || detection.score <= this.config.object_confidence_threshold) continue;
      present.set(eventType, Math.max(present.get(eventType) ?? 0, detection.score));
    }
    if (frame.faces >= 2 || frame.persons >= 2) {
      present.set(SECOND_PERSON, 1);
    }
    const tracked = new Set<ClientEventType>([...this.runs.keys(), ...present.keys()]);
    for (const eventType of tracked) {
      const score = present.get(eventType);
      if (score === undefined) {
        this.runs.delete(eventType);
        continue;
      }
      const run = this.runs.get(eventType) ?? { count: 0, minScore: score, reported: false };
      run.count += 1;
      run.minScore = Math.min(run.minScore, score);
      this.runs.set(eventType, run);
      if (run.reported) continue;
      if (run.count >= this.config.object_consecutive_frames) {
        run.reported = true;
        events.push(
          eventType === SECOND_PERSON
            ? {
                event_type: SECOND_PERSON,
                metadata: { faces: frame.faces, persons: frame.persons },
              }
            : {
                event_type: eventType,
                confidence: run.minScore,
                metadata: { frames: run.count },
              }
        );
      } else {
        confirming = true;
      }
    }

    // Face presence, absence and obstruction.
    const nearUniform = frame.variance < this.config.obstruction_variance_threshold;
    if (frame.faces === 0) {
      if (this.absentSince === null) {
        this.absentSince = frame.at;
        this.absenceNearUniform = false;
        this.moderateReported = false;
        this.extendedReported = false;
      }
      const absentFor = frame.at - this.absentSince;
      if (nearUniform) {
        this.absenceNearUniform = true;
        if (this.obstructedSince === null) {
          this.obstructedSince = frame.at;
          this.obstructionReported = false;
        }
        const obstructedFor = frame.at - this.obstructedSince;
        if (!this.obstructionReported && obstructedFor >= seconds(this.config.obstruction_seconds)) {
          this.obstructionReported = true;
          events.push({
            event_type: "CAMERA_OBSTRUCTED",
            duration_ms: Math.round(obstructedFor),
            metadata: {},
          });
        }
      } else {
        this.obstructedSince = null;
      }
      if (!this.extendedReported && absentFor >= seconds(this.config.face_absent_extended_seconds)) {
        this.extendedReported = true;
        events.push({
          event_type: "FACE_ABSENT_EXTENDED",
          duration_ms: Math.round(absentFor),
          metadata: {},
        });
      } else if (!this.moderateReported && absentFor >= seconds(this.config.face_absent_moderate_seconds)) {
        this.moderateReported = true;
        confirming = true;
        events.push({
          event_type: "FACE_ABSENT_MODERATE",
          duration_ms: Math.round(absentFor),
          metadata: {},
        });
      } else if (absentFor > 0) {
        confirming = true;
      }
    } else if (this.absentSince !== null) {
      const absentFor = frame.at - this.absentSince;
      if (!this.moderateReported) {
        events.push({
          event_type: "FACE_ABSENT_BRIEF",
          duration_ms: Math.round(absentFor),
          metadata: { near_uniform: this.absenceNearUniform },
        });
      }
      this.absentSince = null;
      this.obstructedSince = null;
    }

    // Lighting: a quality note, once per dark episode.
    const dark = frame.luminance < this.config.low_light_luminance_threshold;
    if (dark && !this.lowLight) {
      events.push({ event_type: "LOW_LIGHT", metadata: {} });
    }
    this.lowLight = dark;

    return { events, confirming };
  }

  /**
   * One identity comparison. `distance` is the Euclidean distance between the
   * fresh descriptor and the baseline, or null when no face could be measured
   * (which is an absence, already handled above, not a mismatch).
   */
  observeIdentity(distance: number | null): EventDraft[] {
    if (distance === null) return [];
    const matched = distance <= this.config.face_distance_threshold;
    this.lastIdentity = matched;
    if (matched) return [];
    return [
      {
        event_type: "IDENTITY_CHECK_MISMATCH",
        metadata: { distance_over_threshold: true },
      },
    ];
  }
}

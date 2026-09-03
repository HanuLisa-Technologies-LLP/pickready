/**
 * The events this client may emit, mirrored from
 * `backend/app/services/proctoring/catalog.py` (the entries with
 * `client_emittable=True`) with the consequence path the server assigns to
 * each.
 *
 * THE SERVER DECIDES. The path is carried here for ONE reason: an event on
 * Path A is flushed the moment it is queued rather than on the next batch
 * tick, because the session is about to end and the candidate should learn
 * that from a screen and not from a request that failed a minute later. The
 * client never acts on a path itself; it requests, the server issues.
 *
 * `lib/proctoring/model-assets.test.ts` reads the Python catalog and asserts
 * this table names exactly the client-emittable identifiers, so an event
 * added on one side without the other fails a test rather than a request.
 */

export type ConsequencePath = "A" | "B" | "C";

export const CLIENT_EVENTS = {
  // Path A: proctoring itself defeated; immediate termination.
  CAMERA_OBSTRUCTED: "A",
  FACE_ABSENT_EXTENDED: "A",
  CAMERA_PERMISSION_LOST: "A",
  MIC_PERMISSION_LOST: "A",
  CAMERA_STREAM_FAILED: "A",
  INTEGRITY_CHECK_FAILED: "A",
  // Path B: the shared three-warning counter.
  FULLSCREEN_EXITED: "B",
  WINDOW_FOCUS_LOST: "B",
  DEVICE_DETECTED_PHONE: "B",
  DEVICE_DETECTED_LAPTOP: "B",
  DEVICE_DETECTED_SCREEN: "B",
  SECOND_PERSON_DETECTED: "B",
  FACE_ABSENT_MODERATE: "B",
  MULTIPLE_DISPLAYS_DETECTED: "B",
  // Path C: logged only.
  FACE_ABSENT_BRIEF: "C",
  IDENTITY_CHECK_MISMATCH: "C",
  LOW_LIGHT: "C",
  BLOCKED_ACTION_ATTEMPTED: "C",
  SESSION_QUALITY_DEGRADED: "C",
  INTEGRITY_CHECK_WARNING: "C",
  CAMERA_STREAM_INTERRUPTED: "C",
} as const satisfies Record<string, ConsequencePath>;

export type ClientEventType = keyof typeof CLIENT_EVENTS;

export function isImmediate(eventType: ClientEventType): boolean {
  return CLIENT_EVENTS[eventType] === "A";
}

/**
 * The COCO classes the specification says to act on (section 3.1) and the
 * event each one requests. `book` is listed there too, but the catalog has no
 * event for it, so it is discarded in the worker with the other classes
 * rather than reported under a type that means something else.
 */
export const OBJECT_EVENT_FOR_LABEL: Readonly<Record<string, ClientEventType>> = {
  "cell phone": "DEVICE_DETECTED_PHONE",
  laptop: "DEVICE_DETECTED_LAPTOP",
  tv: "DEVICE_DETECTED_SCREEN",
};

/** The class whose count, with the face count, decides a second person. */
export const PERSON_LABEL = "person";

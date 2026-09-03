/**
 * The messages between the main thread and the two inference workers.
 *
 * A FRAME GOES IN; DETECTIONS COME OUT. Every message from a worker is labels,
 * counts, scores and a few numbers about the frame. There is no message type
 * that carries pixels back, so a frame cannot leave the worker by any path
 * this protocol offers; the worker closes the bitmap it was handed as soon as
 * inference is done.
 *
 * TWO WORKERS, NOT ONE. `@tensorflow/tfjs` 4.x and the `@tensorflow/tfjs-core`
 * 1.7 that `face-api.js` 0.22.2 bundles both register their engine on the
 * same global (`_tfengine`, see each package's `engine.js`), so the second to
 * load in a realm adopts the first's engine and neither works. Each gets its
 * own worker and therefore its own global. Neither is imported on the main
 * thread.
 */

/** One face as the landmarker saw it: a normalised bounding box and the full
 *  landmark set. Kept in this shape so a future gaze module can consume the
 *  landmarks without re-architecting the worker (spec 3.2). */
export interface FaceObservation {
  landmarks: Array<{ x: number; y: number; z: number }>;
}

export interface CvInitMessage {
  type: "init";
  cocoModelUrl: string;
  mediapipeWasmPath: string;
  faceLandmarkerModelPath: string;
  /** Faces the landmarker looks for; the spec wants to know about 2+. */
  numFaces: number;
  /** COCO classes worth reporting. Everything else is discarded in the worker. */
  labels: string[];
}

export interface CvFrameMessage {
  type: "frame";
  id: number;
  bitmap: ImageBitmap;
  timestampMs: number;
}

export type CvInbound = CvInitMessage | CvFrameMessage;

export interface CvReadyMessage {
  type: "ready";
  tfBackend: string;
  landmarkerDelegate: "GPU" | "CPU";
}

export interface CvDetectionsMessage {
  type: "detections";
  id: number;
  objects: Array<{ label: string; score: number }>;
  faces: number;
  persons: number;
  faceObservations: FaceObservation[];
  /** Mean grey level of the frame. */
  luminance: number;
  /** Standard deviation of the grey levels: near zero for a covered lens. */
  variance: number;
  inferenceMs: number;
}

export interface WorkerErrorMessage {
  type: "error";
  id?: number;
  message: string;
}

export type CvOutbound = CvReadyMessage | CvDetectionsMessage | WorkerErrorMessage;

export interface IdentityInitMessage {
  type: "init";
  weightsPath: string;
}

export interface IdentityBaselineMessage {
  type: "baseline";
  id: number;
  bitmap: ImageBitmap;
}

export interface IdentitySetBaselineMessage {
  type: "set-baseline";
  descriptor: number[];
}

export interface IdentityCheckMessage {
  type: "check";
  id: number;
  bitmap: ImageBitmap;
}

export type IdentityInbound =
  | IdentityInitMessage
  | IdentityBaselineMessage
  | IdentitySetBaselineMessage
  | IdentityCheckMessage;

export interface IdentityReadyMessage {
  type: "ready";
}

export interface IdentityDescriptorMessage {
  type: "descriptor";
  id: number;
  /** The 128 floats, or null when no face was found. Never an image. */
  descriptor: number[] | null;
}

export interface IdentityDistanceMessage {
  type: "distance";
  id: number;
  /** Euclidean distance to the baseline, or null when no face was found. */
  distance: number | null;
}

export type IdentityOutbound =
  | IdentityReadyMessage
  | IdentityDescriptorMessage
  | IdentityDistanceMessage
  | WorkerErrorMessage;

/** Where the vendored models are served from. `public/models/README.md`. */
export const MODEL_PATHS = {
  cocoSsdModel: "/models/coco-ssd/model.json",
  mediapipeWasm: "/models/mediapipe/wasm",
  faceLandmarkerModel: "/models/mediapipe/face_landmarker.task",
  faceApiWeights: "/models/face-api",
} as const;

/** The files the worker loads from `MODEL_PATHS.faceApiWeights`. */
export const FACE_API_MODELS = [
  "tiny_face_detector_model",
  "face_landmark_68_model",
  "face_recognition_model",
] as const;

/**
 * Mean and standard deviation of the grey levels of an RGBA buffer. Computed
 * over every pixel; the frame is small enough at sampling rate that the
 * saving from striding would not be worth the sensitivity lost on a lens
 * covered at one edge.
 */
export function greyStatistics(data: Uint8ClampedArray): { luminance: number; variance: number } {
  const pixels = data.length / 4;
  if (pixels === 0) return { luminance: 0, variance: 0 };
  let sum = 0;
  let sumSquares = 0;
  for (let index = 0; index < data.length; index += 4) {
    // Rec. 601 luma weights.
    const grey = 0.299 * data[index] + 0.587 * data[index + 1] + 0.114 * data[index + 2];
    sum += grey;
    sumSquares += grey * grey;
  }
  const mean = sum / pixels;
  const spread = Math.max(0, sumSquares / pixels - mean * mean);
  return { luminance: mean, variance: Math.sqrt(spread) };
}

export function euclideanDistance(a: ArrayLike<number>, b: ArrayLike<number>): number {
  if (a.length !== b.length) {
    throw new Error(`descriptors differ in width: ${a.length} and ${b.length}`);
  }
  let total = 0;
  for (let index = 0; index < a.length; index += 1) {
    const delta = a[index] - b[index];
    total += delta * delta;
  }
  return Math.sqrt(total);
}

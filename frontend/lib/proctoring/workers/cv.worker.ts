/**
 * The camera inference worker: COCO-SSD for objects, MediaPipe Face
 * Landmarker for faces (proctoring spec 3.1, 3.2, 3.6).
 *
 * Runs off the main thread so the assessment never stutters because of
 * proctoring. Receives an ImageBitmap per sampled frame, draws it once into
 * an OffscreenCanvas it owns, reads the pixels for the grey statistics,
 * runs both models, closes the bitmap and posts DETECTIONS ONLY: labels,
 * scores, counts, landmarks and two numbers about the frame. The pixel buffer
 * is a local that goes out of scope at the end of the handler; nothing is
 * kept between frames and nothing is logged.
 *
 * Every model file comes from our own origin (`MODEL_PATHS`); the COCO-SSD
 * `modelUrl` is passed explicitly so the package never reaches for its
 * publisher's host. `face-api.js` is NOT in this worker: see protocol.ts.
 *
 * A classic worker, not a module worker: `@mediapipe/tasks-vision` loads its
 * wasm glue with `importScripts`, which a module worker does not have.
 */
import * as tf from "@tensorflow/tfjs";
import * as cocoSsd from "@tensorflow-models/coco-ssd";
import { FaceLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";

import {
  greyStatistics,
  type CvDetectionsMessage,
  type CvInbound,
  type CvOutbound,
  type FaceObservation,
} from "./protocol";

interface WorkerScope {
  postMessage(message: CvOutbound, transfer?: Transferable[]): void;
  onmessage: ((event: MessageEvent<CvInbound>) => void) | null;
}

const scope = self as unknown as WorkerScope;

let detector: cocoSsd.ObjectDetection | null = null;
let landmarker: FaceLandmarker | null = null;
let labels = new Set<string>();
let canvas: OffscreenCanvas | null = null;
let context: OffscreenCanvasRenderingContext2D | null = null;
/** MediaPipe's video mode needs strictly increasing timestamps. */
let lastTimestamp = -1;

async function initialise(
  cocoModelUrl: string,
  wasmPath: string,
  landmarkerModelPath: string,
  numFaces: number
): Promise<{ tfBackend: string; landmarkerDelegate: "GPU" | "CPU" }> {
  await tf.ready();
  detector = await cocoSsd.load({ base: "lite_mobilenet_v2", modelUrl: cocoModelUrl });
  const fileset = await FilesetResolver.forVisionTasks(wasmPath);
  const options = (delegate: "GPU" | "CPU") => ({
    baseOptions: { modelAssetPath: landmarkerModelPath, delegate },
    runningMode: "VIDEO" as const,
    numFaces,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: false,
  });
  let landmarkerDelegate: "GPU" | "CPU" = "GPU";
  try {
    landmarker = await FaceLandmarker.createFromOptions(fileset, options("GPU"));
  } catch {
    // No usable WebGL2 in this worker. The CPU delegate is the same model at
    // a lower frame rate, which the sampler measures and reports as degraded
    // session quality rather than hiding.
    landmarkerDelegate = "CPU";
    landmarker = await FaceLandmarker.createFromOptions(fileset, options("CPU"));
  }
  return { tfBackend: tf.getBackend(), landmarkerDelegate };
}

async function detect(id: number, bitmap: ImageBitmap, timestampMs: number): Promise<CvDetectionsMessage> {
  if (!detector || !landmarker) {
    throw new Error("frame received before the models were loaded");
  }
  const started = performance.now();
  if (!canvas || canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    context = canvas.getContext("2d", { willReadFrequently: true });
  }
  if (!context) throw new Error("OffscreenCanvas has no 2d context in this worker");
  context.drawImage(bitmap, 0, 0);
  bitmap.close();
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
  const { luminance, variance } = greyStatistics(pixels.data);

  const detections = await detector.detect(pixels);
  const objects: CvDetectionsMessage["objects"] = [];
  let persons = 0;
  for (const detection of detections) {
    if (!labels.has(detection.class)) continue;
    if (detection.class === "person") persons += 1;
    objects.push({ label: detection.class, score: detection.score });
  }

  // Video mode requires monotonic timestamps; two frames captured within the
  // same millisecond would otherwise be refused.
  const timestamp = Math.max(Math.round(timestampMs), lastTimestamp + 1);
  lastTimestamp = timestamp;
  const faces = landmarker.detectForVideo(pixels, timestamp);
  const faceObservations: FaceObservation[] = faces.faceLandmarks.map((points) => ({
    landmarks: points.map((point) => ({ x: point.x, y: point.y, z: point.z })),
  }));

  context.clearRect(0, 0, canvas.width, canvas.height);
  return {
    type: "detections",
    id,
    objects,
    faces: faceObservations.length,
    persons,
    faceObservations,
    luminance,
    variance,
    inferenceMs: performance.now() - started,
  };
}

scope.onmessage = (event: MessageEvent<CvInbound>) => {
  const message = event.data;
  if (message.type === "init") {
    labels = new Set(message.labels);
    initialise(
      message.cocoModelUrl,
      message.mediapipeWasmPath,
      message.faceLandmarkerModelPath,
      message.numFaces
    ).then(
      (ready) => scope.postMessage({ type: "ready", ...ready }),
      (error: unknown) =>
        scope.postMessage({ type: "error", message: error instanceof Error ? error.message : String(error) })
    );
    return;
  }
  detect(message.id, message.bitmap, message.timestampMs).then(
    (result) => scope.postMessage(result),
    (error: unknown) => {
      message.bitmap.close();
      scope.postMessage({
        type: "error",
        id: message.id,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  );
};

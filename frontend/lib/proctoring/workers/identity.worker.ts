/**
 * The identity worker: face-api.js for the 128-dimension descriptor
 * (proctoring spec 3.3).
 *
 * WHY ITS OWN WORKER. face-api.js 0.22.2 bundles `@tensorflow/tfjs-core`
 * 1.7.0, and that core and the 4.x core in the camera worker both install
 * their engine on the same global. In one realm the second to load adopts
 * the first's engine and fails. So this library lives alone in this worker
 * and is never imported on the main thread.
 *
 * WHY `setEnv` RATHER THAN `monkeyPatch`. face-api's `env.monkeyPatch` first
 * calls `initialize()`, which recognises a browser (by `window` and
 * `document`) or Node (by `process.version`) and nothing else; a worker is
 * neither, so it throws before it can be patched. `setEnv` installs a whole
 * environment, which is what the patch would have produced: an
 * OffscreenCanvas for `Canvas`, a canvas factory, `fetch` for the weights,
 * and stand-ins for the element types a worker does not have.
 *
 * What is stored: the BASELINE DESCRIPTOR, in this worker's memory, so each
 * check can be compared without the main thread holding it. A descriptor is
 * a vector the recognition network produced. It cannot be turned back into a
 * face. The bitmap each check arrives on is drawn, measured, and closed.
 */
import * as faceapi from "face-api.js";

import {
  FACE_API_MODELS,
  euclideanDistance,
  type IdentityInbound,
  type IdentityOutbound,
} from "./protocol";

interface WorkerScope {
  postMessage(message: IdentityOutbound): void;
  onmessage: ((event: MessageEvent<IdentityInbound>) => void) | null;
  fetch: typeof fetch;
}

const scope = self as unknown as WorkerScope;

let baseline: Float32Array | null = null;
let ready = false;

class NotInAWorker {
  constructor() {
    throw new Error("face-api asked for a DOM element inside a worker");
  }
}

function installEnvironment(): void {
  faceapi.env.setEnv({
    Canvas: OffscreenCanvas as unknown as typeof HTMLCanvasElement,
    CanvasRenderingContext2D:
      OffscreenCanvasRenderingContext2D as unknown as typeof CanvasRenderingContext2D,
    Image: NotInAWorker as unknown as typeof HTMLImageElement,
    ImageData,
    Video: NotInAWorker as unknown as typeof HTMLVideoElement,
    createCanvasElement: () => new OffscreenCanvas(1, 1) as unknown as HTMLCanvasElement,
    createImageElement: () => {
      throw new Error("face-api asked for an image element inside a worker");
    },
    fetch: (url, init) => scope.fetch(url, init),
    readFile: () => {
      throw new Error("face-api asked for the file system inside a worker");
    },
  });
}

async function loadModels(weightsPath: string): Promise<void> {
  installEnvironment();
  const loaders: Record<(typeof FACE_API_MODELS)[number], (uri: string) => Promise<void>> = {
    tiny_face_detector_model: (uri) => faceapi.nets.tinyFaceDetector.loadFromUri(uri),
    face_landmark_68_model: (uri) => faceapi.nets.faceLandmark68Net.loadFromUri(uri),
    face_recognition_model: (uri) => faceapi.nets.faceRecognitionNet.loadFromUri(uri),
  };
  for (const model of FACE_API_MODELS) {
    await loaders[model](weightsPath);
  }
  ready = true;
}

async function describe(bitmap: ImageBitmap): Promise<Float32Array | null> {
  if (!ready) throw new Error("bitmap received before the recognition models were loaded");
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("OffscreenCanvas has no 2d context in this worker");
  context.drawImage(bitmap, 0, 0);
  bitmap.close();
  const result = await faceapi
    .detectSingleFace(canvas as unknown as HTMLCanvasElement, new faceapi.TinyFaceDetectorOptions())
    .withFaceLandmarks()
    .withFaceDescriptor();
  context.clearRect(0, 0, canvas.width, canvas.height);
  return result ? result.descriptor : null;
}

scope.onmessage = (event: MessageEvent<IdentityInbound>) => {
  const message = event.data;
  const fail = (error: unknown, id?: number) =>
    scope.postMessage({
      type: "error",
      id,
      message: error instanceof Error ? error.message : String(error),
    });

  if (message.type === "init") {
    loadModels(message.weightsPath).then(() => scope.postMessage({ type: "ready" }), fail);
    return;
  }
  if (message.type === "set-baseline") {
    baseline = Float32Array.from(message.descriptor);
    return;
  }
  if (message.type === "baseline") {
    describe(message.bitmap).then((descriptor) => {
      if (descriptor) baseline = descriptor;
      scope.postMessage({
        type: "descriptor",
        id: message.id,
        descriptor: descriptor ? Array.from(descriptor) : null,
      });
    }, (error: unknown) => fail(error, message.id));
    return;
  }
  describe(message.bitmap).then((descriptor) => {
    if (!baseline) throw new Error("identity check requested before a baseline was set");
    scope.postMessage({
      type: "distance",
      id: message.id,
      distance: descriptor ? euclideanDistance(descriptor, baseline) : null,
    });
  }, (error: unknown) => fail(error, message.id));
};

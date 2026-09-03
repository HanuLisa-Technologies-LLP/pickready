# Vendored proctoring models

The proctoring client (`lib/proctoring/`) runs every camera inference in the
candidate's browser and loads its models from THIS directory, on our own
origin. Nothing is fetched from a third-party host at runtime: an assessment
must not depend on somebody else's CDN being reachable mid-session, and a
third party must not learn when a candidate is being assessed.

`manifest.json` pins every file by SHA-256, size and source.
`scripts/vendor-proctoring-models.mjs` verifies the directory against it and
refuses a download whose hash differs; `--record` is the one deliberate way to
re-pin. `lib/proctoring/model-assets.test.ts` fails the test suite if a path
the client references is missing or has drifted from its pin.

The frame itself never leaves the worker these models run in, and no frame,
image or descriptor of anything but a face at session start is stored
anywhere. See the proctoring specification, sections 1 and 10.

## coco-ssd/

COCO-SSD, `lite_mobilenet_v2` variant (SSDLite on a MobileNet v2 backbone),
as published by the TensorFlow.js models project and loaded by
`@tensorflow-models/coco-ssd` 2.2.3 through `modelUrl`.

- Source: `https://storage.googleapis.com/tfjs-models/savedmodel/ssdlite_mobilenet_v2/model.json`
  plus the five weight shards that file's `weightsManifest` names. This is
  the exact URL the package itself would fetch for this variant.
- Licence: Apache License 2.0 (tensorflow/tfjs-models).
- Used for: `cell phone`, `laptop`, `tv` and `person` detections. Every other
  class is discarded in the worker.

## mediapipe/

MediaPipe Face Landmarker and the `@mediapipe/tasks-vision` WebAssembly
runtime it needs.

- `face_landmarker.task`: Google's `face_landmarker` model, float16, version 1,
  from `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`.
  Licence: Apache License 2.0 (google-ai-edge/mediapipe).
- `wasm/`: copied from the installed `@mediapipe/tasks-vision` package
  (version recorded in `manifest.json`), so the runtime and its wasm are the
  same release. `FilesetResolver.forVisionTasks` chooses the SIMD build when
  the browser supports it and the `nosimd` build otherwise. Licence: Apache
  License 2.0.
- Used for: face presence, face count (`numFaces: 3`), and the landmark set
  kept in a forward-compatible shape for a future gaze module.

## face-api/

The three weight sets `face-api.js` 0.22.2 needs for a 128-dimension identity
descriptor: `tiny_face_detector_model`, `face_landmark_68_model` and
`face_recognition_model`, each a `-weights_manifest.json` and its shards.

- Source: `https://raw.githubusercontent.com/justadudewhohacks/face-api.js/master/weights/`.
- Licence: MIT (justadudewhohacks/face-api.js).
- Used for: the baseline descriptor captured at the system check and the
  periodic identity comparison by Euclidean distance. A descriptor is a
  vector produced by the recognition network; it is not a photograph and
  cannot be inverted into one.

## Updating

Change the pinned URL in `scripts/vendor-proctoring-models.mjs` if the
source moves, run `node scripts/vendor-proctoring-models.mjs --record`, review
the `manifest.json` diff, and commit the files with it.

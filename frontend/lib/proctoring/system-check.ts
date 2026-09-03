/**
 * The system check (proctoring spec 8.2): six questions, each answered pass
 * or fail with a plain-language fix.
 *
 * The assessment does not begin until every row passes, and a row that fails
 * has to tell the candidate what to do about it rather than leaving them at
 * a dead end. So each check carries its own instruction, written for the
 * person reading it and not for the engineer who will debug it.
 *
 * WHAT THE CHECK KEEPS. The camera and microphone streams it opened, so the
 * session can start on them without asking the browser twice; the inference
 * client, which has already paid for loading the models; the baseline face
 * descriptor, a 128-float vector and not an image; and the measured frame
 * rate, so a slow device is recorded as degraded rather than refused. A slow
 * device passes "inference performance" as long as the models load and
 * answer; the rate decides quality, never eligibility (spec 3.6).
 */
import { type DeviceContextIn, type SystemCheckIn } from "./api";
import { CameraMonitor, isPermissionDenied, openCamera } from "./camera";
import { OBJECT_EVENT_FOR_LABEL, PERSON_LABEL } from "./catalog";
import type { ProctoringClientConfig } from "./config";
import { seconds } from "./config";
import { DetectionRules, type FrameDetections } from "./detections";
import { isExtended } from "./displays";
import { isFullscreenSupported } from "./focus";
import { openMicrophone } from "./audio";
import { InferenceClient } from "./worker-client";

export type CheckKey = keyof Omit<SystemCheckIn, "measured_fps">;

export interface CheckRow {
  key: CheckKey;
  label: string;
  passed: boolean | null;
  /** What to do about it. Present only when the row failed. */
  fix: string | null;
}

export const CHECK_LABELS: Record<CheckKey, string> = {
  camera: "Camera available and permission granted",
  microphone: "Microphone available and permission granted",
  browser_supported: "Browser supported",
  fullscreen_supported: "Fullscreen supported",
  face_detected: "Face detected in frame, adequate lighting",
  inference_adequate: "Monitoring performance adequate",
};

export const CHECK_ORDER: CheckKey[] = [
  "camera",
  "microphone",
  "browser_supported",
  "fullscreen_supported",
  "face_detected",
  "inference_adequate",
];

export const FIX_INSTRUCTIONS = {
  camera_denied:
    "Allow camera access for this site in your browser's address bar or site settings, then try again.",
  camera_missing:
    "No camera could be opened. Connect a webcam, close any other app that is using it, then try again.",
  microphone_denied:
    "Allow microphone access for this site in your browser's address bar or site settings, then try again.",
  microphone_missing:
    "No microphone could be opened. Connect one, close any other app that is using it, then try again.",
  browser:
    "This browser cannot run the monitoring. Use a current version of Chrome, Edge or Firefox on a laptop or desktop computer.",
  fullscreen:
    "This browser cannot enter fullscreen. Use a current version of Chrome, Edge or Firefox, not an embedded or in-app browser.",
  no_face:
    "We could not see your face clearly. Sit facing the camera, about an arm's length away, then try again.",
  low_light:
    "The picture is too dark. Turn on a light in front of you or move somewhere brighter, then try again.",
  models:
    "The monitoring could not start on this device. Check your connection, close other tabs and programs, then try again.",
} as const;

/** How many faces the landmarker looks for: enough to notice a second and a
 *  third person, which is all the rules ask. */
export const LANDMARKER_FACES = 3;

export interface SystemCheckOutcome {
  rows: CheckRow[];
  payload: SystemCheckIn;
  allPassed: boolean;
  camera: MediaStream | null;
  microphone: MediaStream | null;
  inference: InferenceClient | null;
  faceDescriptor: number[] | null;
  deviceContext: DeviceContextIn;
}

export function isBrowserSupported(win: Window = window): boolean {
  const nav = win.navigator;
  // Read off the window as a bag of globals: `Worker`, `OffscreenCanvas`,
  // `MediaRecorder` and `WebAssembly` are ambient declarations rather than
  // members of the DOM `Window` interface, and the whole question here is
  // whether THIS browser has them.
  const globals = win as unknown as Record<string, unknown>;
  return Boolean(
    nav.mediaDevices &&
      typeof nav.mediaDevices.getUserMedia === "function" &&
      typeof globals.Worker === "function" &&
      typeof globals.OffscreenCanvas === "function" &&
      typeof win.createImageBitmap === "function" &&
      typeof globals.MediaRecorder === "function" &&
      typeof globals.WebAssembly === "object"
  );
}

export function buildRows(payload: SystemCheckIn, fixes: Partial<Record<CheckKey, string>>): CheckRow[] {
  return CHECK_ORDER.map((key) => ({
    key,
    label: CHECK_LABELS[key],
    passed: payload[key],
    fix: payload[key] ? null : (fixes[key] ?? null),
  }));
}

function deviceContext(win: Window, camera: MediaStream | null, webgl: boolean | null): DeviceContextIn {
  const settings = camera?.getVideoTracks()[0]?.getSettings();
  const nav = win.navigator as Navigator & { userAgentData?: { platform?: string } };
  return {
    user_agent: nav.userAgent.slice(0, 400),
    platform: (nav.userAgentData?.platform ?? nav.platform ?? "").slice(0, 100),
    screen_count: isExtended(win) ? 2 : 1,
    screen_width: win.screen.width || null,
    screen_height: win.screen.height || null,
    camera_width: settings?.width ?? null,
    camera_height: settings?.height ?? null,
    hardware_concurrency: nav.hardwareConcurrency || null,
    webgl,
  };
}

/**
 * Run all six checks. Media and models the check opened are RETURNED, not
 * released, so the session can start on them; the caller releases them if
 * the candidate never starts.
 */
export async function runSystemCheck(
  config: ProctoringClientConfig,
  win: Window = window,
  inferenceFactory: () => InferenceClient = () =>
    new InferenceClient([...Object.keys(OBJECT_EVENT_FOR_LABEL), PERSON_LABEL], LANDMARKER_FACES)
): Promise<SystemCheckOutcome> {
  const fixes: Partial<Record<CheckKey, string>> = {};
  const nav = win.navigator;

  const browserSupported = isBrowserSupported(win);
  if (!browserSupported) fixes.browser_supported = FIX_INSTRUCTIONS.browser;
  const fullscreenSupported = isFullscreenSupported(win.document);
  if (!fullscreenSupported) fixes.fullscreen_supported = FIX_INSTRUCTIONS.fullscreen;

  let camera: MediaStream | null = null;
  let microphone: MediaStream | null = null;
  if (browserSupported) {
    try {
      camera = await openCamera(nav);
    } catch (error) {
      fixes.camera = isPermissionDenied(error) ? FIX_INSTRUCTIONS.camera_denied : FIX_INSTRUCTIONS.camera_missing;
    }
    try {
      microphone = await openMicrophone(nav);
    } catch (error) {
      fixes.microphone = isPermissionDenied(error)
        ? FIX_INSTRUCTIONS.microphone_denied
        : FIX_INSTRUCTIONS.microphone_missing;
    }
  } else {
    fixes.camera = FIX_INSTRUCTIONS.browser;
    fixes.microphone = FIX_INSTRUCTIONS.browser;
  }

  let inference: InferenceClient | null = null;
  let webgl: boolean | null = null;
  let faceDescriptor: number[] | null = null;
  let measuredFps: number | null = null;
  let faceDetected = false;
  let inferenceAdequate = false;

  if (browserSupported && camera) {
    inference = inferenceFactory();
    let lastFrame: FrameDetections | null = null;
    let monitor: CameraMonitor | null = null;
    try {
      const ready = await inference.ready();
      webgl = ready.tfBackend === "webgl";
      monitor = new CameraMonitor({
        config,
        inference,
        rules: new DetectionRules(config),
        onEvent: () => undefined,
        onFrame: (frame) => {
          lastFrame = frame;
        },
        identityChecks: false,
        navigator: nav,
        document: win.document,
      });
      await monitor.start(camera);
      await new Promise((resolve) => setTimeout(resolve, seconds(config.confirming_window_seconds)));
      measuredFps = monitor.status().measuredFps;
      inferenceAdequate = lastFrame !== null;
      if (lastFrame !== null) {
        const frame: FrameDetections = lastFrame;
        if (frame.faces === 0) {
          fixes.face_detected = FIX_INSTRUCTIONS.no_face;
        } else if (frame.luminance < config.low_light_luminance_threshold) {
          fixes.face_detected = FIX_INSTRUCTIONS.low_light;
        } else {
          faceDescriptor = await inference.baseline(await monitor.snapshot());
          if (faceDescriptor === null) fixes.face_detected = FIX_INSTRUCTIONS.no_face;
          faceDetected = faceDescriptor !== null;
        }
      } else {
        fixes.face_detected = FIX_INSTRUCTIONS.models;
      }
    } catch {
      fixes.inference_adequate = FIX_INSTRUCTIONS.models;
      fixes.face_detected = FIX_INSTRUCTIONS.models;
      inference.terminate();
      inference = null;
    } finally {
      monitor?.detach();
    }
    if (!inferenceAdequate && !fixes.inference_adequate) fixes.inference_adequate = FIX_INSTRUCTIONS.models;
  } else {
    fixes.face_detected = fixes.camera ?? FIX_INSTRUCTIONS.browser;
    fixes.inference_adequate = fixes.camera ?? FIX_INSTRUCTIONS.browser;
  }

  const payload: SystemCheckIn = {
    camera: camera !== null,
    microphone: microphone !== null,
    browser_supported: browserSupported,
    fullscreen_supported: fullscreenSupported,
    face_detected: faceDetected,
    inference_adequate: inferenceAdequate,
    measured_fps: measuredFps,
  };
  const rows = buildRows(payload, fixes);
  return {
    rows,
    payload,
    allPassed: rows.every((row) => row.passed === true),
    camera,
    microphone,
    inference,
    faceDescriptor,
    deviceContext: deviceContext(win, camera, webgl),
  };
}

export function releaseOutcome(outcome: SystemCheckOutcome): void {
  outcome.camera?.getTracks().forEach((track) => track.stop());
  outcome.microphone?.getTracks().forEach((track) => track.stop());
  outcome.inference?.terminate();
}

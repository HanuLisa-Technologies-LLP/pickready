/**
 * The four proctoring routes the client calls, and the shapes they exchange.
 *
 * Mirrors `backend/app/schemas/proctoring.py`. The candidate side is EVENTS
 * ONLY: an identifier, a time, a duration, a confidence and a small metadata
 * object. There is no field for a frame, an image or any text a candidate
 * typed, and the audio chunk is the one piece of media that leaves the browser
 * at all, to the one route that analyses it in memory and destroys it.
 */
import { apiFetch, apiPost, ApiError } from "@/lib/api";

import type { ClientEventType } from "./catalog";
import { parseClientConfig, type ProctoringClientConfig } from "./config";

const BASE = "/api/v2/proctoring";

export interface DeviceContextIn {
  user_agent: string;
  platform: string;
  screen_count: number | null;
  screen_width: number | null;
  screen_height: number | null;
  camera_width: number | null;
  camera_height: number | null;
  hardware_concurrency: number | null;
  webgl: boolean | null;
}

/** The six checks of spec 8.2, plus the measured inference rate so a slow
 *  device is recorded as degraded rather than refused. */
export interface SystemCheckIn {
  camera: boolean;
  microphone: boolean;
  browser_supported: boolean;
  fullscreen_supported: boolean;
  face_detected: boolean;
  inference_adequate: boolean;
  measured_fps: number | null;
}

export interface SessionCreateIn {
  consent: true;
  device_context: DeviceContextIn;
  system_check: SystemCheckIn;
  /** The 128-float descriptor from the system check. A vector, not an image. */
  face_descriptor: number[];
}

export type WarningPolicy = "terminate" | "continue_and_note";

export interface SessionOut {
  session_id: string;
  conversation_id: string;
  status: string;
  warnings_used: number;
  max_warnings: number;
  warning_policy: WarningPolicy;
  consented_at: string;
  config: ProctoringClientConfig;
  audio_analysis_available: boolean;
}

export interface EventIn {
  event_type: ClientEventType;
  occurred_at: string;
  duration_ms?: number | null;
  confidence?: number | null;
  question_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface WarningOut {
  number: number;
  max_warnings: number;
  event_type: string;
  message: string;
  final: boolean;
}

export interface TerminationOut {
  reason_code: string;
  message: string;
}

export interface IngestOut {
  accepted: number;
  warnings_used: number;
  max_warnings: number;
  status: string;
  warning: WarningOut | null;
  termination: TerminationOut | null;
}

export interface MonitoringStatus {
  camera: boolean;
  microphone: boolean;
  models: boolean;
  handlers: boolean;
}

export interface HeartbeatIn {
  identity_matched: boolean | null;
  monitoring: MonitoringStatus;
}

export interface HeartbeatOut {
  status: string;
  warnings_used: number;
  server_time: string;
  interval_seconds: number;
  termination: TerminationOut | null;
}

export interface AudioChunkOut {
  analysed: boolean;
  status: string;
  warnings_used: number;
  warning: WarningOut | null;
  termination: TerminationOut | null;
}

export async function createSession(linkId: string, body: SessionCreateIn): Promise<SessionOut> {
  const session = await apiPost<SessionOut>(`${BASE}/links/${linkId}/session`, body);
  return { ...session, config: parseClientConfig(session.config) };
}

export function postEvents(sessionId: string, events: EventIn[]): Promise<IngestOut> {
  return apiPost<IngestOut>(`${BASE}/sessions/${sessionId}/events`, { events });
}

export function postHeartbeat(sessionId: string, body: HeartbeatIn): Promise<HeartbeatOut> {
  return apiPost<HeartbeatOut>(`${BASE}/sessions/${sessionId}/heartbeat`, body);
}

/**
 * The audio chunk, as multipart. `apiFetch` rather than `apiUpload` because the
 * response has to be read whether or not it was 2xx: a 409 here means the
 * session ended and the caller needs the message, not a generic failure.
 */
export async function postAudioChunk(sessionId: string, chunk: Blob): Promise<AudioChunkOut> {
  const form = new FormData();
  form.append("chunk", chunk, "chunk.webm");
  const response = await apiFetch(`${BASE}/sessions/${sessionId}/audio`, {
    method: "POST",
    body: form,
  });
  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload as AudioChunkOut;
}

/** A 409 from any session route: the session is over on the server. */
export function isSessionEnded(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409;
}

/** A request the server refused as malformed. Retrying cannot repair it. */
export function isClientFault(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 409 &&
    error.status !== 429
  );
}

"use client";

// The video interview (dual-mode spec sections 4 and 5).
//
// One continuous recording, one question on screen at a time, moved forward
// by the candidate's own Next control. Every question display is stamped
// SERVER-side (`POST .../video/mark`): the transcript is later segmented by
// those marks, so a mark that failed to reach the server blocks the Next
// control rather than silently producing an answer filed under the wrong
// question.
//
// Rendered INSIDE the proctoring shell, exactly as the conversational player
// is: proctoring is mandatory in both modes, its consent and system check
// (camera and microphone included) run first, and it stores no media. The
// RECORDING here is the separately consented artifact of this mode; this
// component opens its own stream for MediaRecorder and closes it when the
// interview ends.
//
// The recording stays in this browser until the candidate finishes, then goes
// up as one upload with real progress. A failed upload keeps the bytes here
// and offers a retry; nothing pretends an upload happened. After submission
// the screen polls the server's honest processing status and renders exactly
// what it is told.

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Loader2, Video } from "lucide-react";

import { useProctoring } from "@/components/proctoring/proctoring-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost, apiUploadWithProgress } from "@/lib/api";
import type { McqPayloadView } from "@/lib/assessment/contracts";
import { timeAllocationPhrase } from "@/lib/assessment/time-guidance";
import type { VideoInterviewStart, VideoRecordingStatus } from "@/lib/types";

type Phase =
  | "starting"
  | "start_failed"
  | "recording"
  | "uploading"
  | "upload_failed"
  | "processing"
  | "done";

const STATUS_POLL_MS = 5000;
/** Statuses that end the polling: the pipeline has either finished or
 *  honestly failed, and either way the candidate's part is over. */
const TERMINAL = new Set([
  "ready",
  "processing_failed",
  "transcription_failed",
  "compression_failed",
  "storage_failed",
]);

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function VideoInterview({ linkId }: { linkId: string }) {
  const bridge = useProctoring();
  const [phase, setPhase] = React.useState<Phase>("starting");
  const [startError, setStartError] = React.useState<string | null>(null);
  const [session, setSession] = React.useState<VideoInterviewStart | null>(null);
  const [index, setIndex] = React.useState(0);
  const [marking, setMarking] = React.useState(false);
  const [markError, setMarkError] = React.useState<string | null>(null);
  const [elapsed, setElapsed] = React.useState(0);
  const [uploadPercent, setUploadPercent] = React.useState(0);
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const [processing, setProcessing] = React.useState<VideoRecordingStatus | null>(null);

  const videoRef = React.useRef<HTMLVideoElement | null>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const mimeRef = React.useRef<string>("video/webm");
  /** The finished recording, kept until the upload verifiably succeeds. */
  const blobRef = React.useRef<Blob | null>(null);
  const startedOnce = React.useRef(false);

  const stopStream = React.useCallback(() => {
    recorderRef.current?.state !== "inactive" && recorderRef.current?.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  // Accidental-loss guard (spec 12): while the recording or its upload is the
  // only copy, leaving the page costs the interview and the browser says so.
  React.useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (phase === "recording" || phase === "uploading" || phase === "upload_failed") {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [phase]);

  // Whatever happens, the camera light goes out with the component.
  React.useEffect(() => () => stopStream(), [stopStream]);

  // The interview clock, shown beside the recording indicator.
  React.useEffect(() => {
    if (phase !== "recording") return;
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const start = React.useCallback(async () => {
    setStartError(null);
    setPhase("starting");
    try {
      const opened = await apiPost<VideoInterviewStart>(
        `/api/v2/assessments/conversations/links/${linkId}/video/start`
      );
      if (opened.questions.length === 0) {
        throw new Error(
          "Your questions are still being prepared. Please try again in a moment."
        );
      }
      // The camera and microphone passed the proctoring system check to get
      // here; this stream is the RECORDER's own, consented separately.
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      const preferred = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
      const mime =
        preferred.find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? "";
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mimeRef.current = recorder.mimeType || "video/webm";
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorderRef.current = recorder;
      recorder.start(1000);
      setSession(opened);
      setIndex(0);
      setElapsed(0);
      setPhase("recording");
      // The first question is on screen the moment recording starts; stamp it.
      await apiPost(
        `/api/v2/assessments/conversations/${opened.conversation_id}/video/mark`,
        { question_id: opened.questions[0].question.id }
      );
    } catch (error) {
      stopStream();
      setStartError(
        error instanceof Error
          ? error.message
          : "The video interview could not be started. Please try again."
      );
      setPhase("start_failed");
    }
  }, [linkId, stopStream]);

  React.useEffect(() => {
    if (startedOnce.current) return;
    startedOnce.current = true;
    void start();
  }, [start]);

  const advance = React.useCallback(async () => {
    if (!session || marking) return;
    const next = index + 1;
    if (next >= session.questions.length) return;
    setMarking(true);
    setMarkError(null);
    try {
      // The mark FIRST, the screen second: the server's stamp is what the
      // transcript is segmented by, so a question must never be on screen
      // before its mark exists.
      await apiPost(
        `/api/v2/assessments/conversations/${session.conversation_id}/video/mark`,
        { question_id: session.questions[next].question.id }
      );
      setIndex(next);
    } catch (error) {
      setMarkError(
        error instanceof Error
          ? error.message
          : "The next question could not be recorded with the server. Please retry."
      );
    } finally {
      setMarking(false);
    }
  }, [index, marking, session]);

  const pollStatus = React.useCallback(
    (conversationId: string) => {
      let cancelled = false;
      const tick = async () => {
        try {
          const status = await apiGet<VideoRecordingStatus>(
            `/api/v2/assessments/conversations/${conversationId}/video/status`
          );
          if (cancelled) return;
          setProcessing(status);
          if (TERMINAL.has(status.status)) {
            setPhase("done");
            return;
          }
        } catch {
          // A missed poll is a missed poll; the next one answers.
        }
        if (!cancelled) window.setTimeout(() => void tick(), STATUS_POLL_MS);
      };
      void tick();
      return () => {
        cancelled = true;
      };
    },
    []
  );

  const upload = React.useCallback(
    async (blob: Blob) => {
      if (!session) return;
      setPhase("uploading");
      setUploadError(null);
      setUploadPercent(0);
      try {
        const form = new FormData();
        const extension = mimeRef.current.includes("mp4") ? "mp4" : "webm";
        form.append("file", blob, `recording.${extension}`);
        const stored = await apiUploadWithProgress<VideoRecordingStatus>(
          `/api/v2/assessments/conversations/${session.conversation_id}/video/upload`,
          form,
          setUploadPercent
        );
        if (stored.status === "upload_failed") {
          setUploadError(stored.message);
          setPhase("upload_failed");
          return;
        }
        blobRef.current = null;
        const finalized = await apiPost<VideoRecordingStatus>(
          `/api/v2/assessments/conversations/${session.conversation_id}/video/finalize`
        );
        setProcessing(finalized);
        setPhase("processing");
        bridge.onConversationEnded("completed");
        pollStatus(session.conversation_id);
      } catch (error) {
        setUploadError(
          error instanceof Error
            ? error.message
            : "The upload did not complete. Your recording is still in this browser; please retry."
        );
        setPhase("upload_failed");
      }
    },
    [bridge, pollStatus, session]
  );

  const finish = React.useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || !session) return;
    const conclude = () => {
      const blob = new Blob(chunksRef.current, { type: mimeRef.current });
      blobRef.current = blob;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      recorderRef.current = null;
      void upload(blob);
    };
    if (recorder.state === "inactive") {
      conclude();
      return;
    }
    recorder.onstop = conclude;
    recorder.stop();
  }, [session, upload]);

  const retryUpload = React.useCallback(() => {
    const blob = blobRef.current;
    if (blob) void upload(blob);
  }, [upload]);

  if (phase === "starting") {
    return (
      <div className="mx-auto flex max-w-2xl items-center gap-3 py-16 text-sm leading-6">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Preparing your video interview...
      </div>
    );
  }

  if (phase === "start_failed") {
    return (
      <div className="mx-auto max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle>The video interview could not start</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm leading-6">{startError}</p>
            <Button onClick={() => void start()}>Try again</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (phase === "processing" || phase === "done") {
    const ready = processing?.status === "ready";
    return (
      <div className="mx-auto max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {phase === "done" && ready ? (
                <CheckCircle2 className="h-5 w-5 text-teal-700" aria-hidden />
              ) : (
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
              )}
              {ready ? "Interview complete" : "Interview submitted"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm leading-6">
              {processing?.message ??
                "Your recording was uploaded and is being processed."}
            </p>
            <p className="text-sm leading-6">
              You can leave this page; processing continues on its own.
            </p>
            <Button variant="outline" asChild>
              <Link href="/portal/applications">Back to Applied Jobs</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (phase === "uploading" || phase === "upload_failed") {
    return (
      <div className="mx-auto max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle>
              {phase === "uploading" ? "Uploading your interview" : "The upload did not complete"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {phase === "uploading" ? (
              <>
                <div className="h-2 w-full overflow-hidden rounded bg-navy-100">
                  <div
                    className="h-full bg-navy-600 transition-all"
                    style={{ width: `${uploadPercent}%` }}
                  />
                </div>
                <p className="text-sm leading-6">
                  Keep this page open until the upload finishes.
                </p>
              </>
            ) : (
              <>
                <p role="alert" className="flex items-start gap-2 text-sm leading-6">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                  <span>{uploadError}</span>
                </p>
                <Button onClick={retryUpload}>Retry the upload</Button>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── phase === "recording" ──────────────────────────────────────────────────
  const current = session!.questions[index];
  const total = session!.questions.length;
  const last = index + 1 >= total;
  const payload = current.question.payload as Partial<McqPayloadView>;
  const options = Array.isArray(payload.options) ? payload.options : null;

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="space-y-3">
        <div className="relative overflow-hidden rounded-lg bg-navy-900">
          {/* The candidate's own preview, muted so it cannot feed back. */}
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="aspect-video w-full object-cover"
          />
          <span className="absolute left-3 top-3 flex items-center gap-2 rounded bg-navy-900/80 px-2 py-1 text-xs font-medium text-white">
            <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
            Recording {formatElapsed(elapsed)}
          </span>
        </div>
        <p className="flex items-center gap-2 text-sm leading-6">
          <Video className="h-4 w-4" aria-hidden />
          Answer out loud. Your whole interview is one recording.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Question {index + 1} of {total}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-base leading-7">{current.prompt}</p>
          {options ? (
            <ul className="space-y-2">
              {options.map((option) => (
                <li key={option.id} className="flex gap-3 text-sm leading-6">
                  <span aria-hidden className="mt-2 h-1 w-4 shrink-0 bg-teal-600" />
                  <span>{option.text}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-sm leading-6">
            {timeAllocationPhrase(current.question.time_allocation_seconds)}
          </p>
          {markError ? (
            <p role="alert" className="text-sm font-medium leading-6">
              {markError}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            {last ? (
              <Button size="lg" disabled={marking} onClick={finish}>
                Finish and submit my interview
              </Button>
            ) : (
              <Button size="lg" disabled={marking} onClick={() => void advance()}>
                {marking ? "Saving..." : "Next question"}
              </Button>
            )}
          </div>
          <p className="text-sm leading-6">
            You cannot return to an earlier question, just as in a live
            interview.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import * as React from "react";
import { Camera, MonitorUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const PROCTORING_ENABLED =
  process.env.NEXT_PUBLIC_PROCTORING_ENABLED === "true";

export function OptionalProctoringConsent({
  enabled = PROCTORING_ENABLED,
}: {
  enabled?: boolean;
}) {
  const [choice, setChoice] = React.useState<"pending" | "accepted" | "declined">("pending");
  const streams = React.useRef<MediaStream[]>([]);

  React.useEffect(
    () => () => streams.current.forEach((stream) => stream.getTracks().forEach((track) => track.stop())),
    []
  );

  if (!enabled || choice !== "pending") {
    return choice === "accepted" ? (
      <p role="status" className="mb-4 text-xs">
        Optional identity and screen capture enabled. Audio is never captured.
      </p>
    ) : null;
  }

  const accept = async () => {
    const camera = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" },
      audio: false,
    });
    const screen = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false,
    });
    streams.current = [camera, screen];
    setChoice("accepted");
  };

  return (
    <Card className="mb-5" data-testid="optional-proctoring-consent">
      <CardHeader>
        <CardTitle>Optional identity and screen capture</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>
          This is an assessment aid only—not an interview or background check.
          If you agree, PickReady may use your webcam for an identity check and
          capture the assessment screen. Audio is never requested or recorded.
        </p>
        <p>
          Participation is optional. Declining has zero effect on questions,
          scoring, ranking, eligibility, or your application.
        </p>
        <p>
          Under India&apos;s Digital Personal Data Protection Act, 2023, you
          may withdraw consent and request access, correction, or erasure
          through the contact channel in our Privacy Notice, subject to
          applicable legal obligations.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void accept()}>
            <Camera className="h-4 w-4" aria-hidden />
            Allow optional capture
          </Button>
          <Button variant="outline" onClick={() => setChoice("declined")}>
            Continue without capture
          </Button>
        </div>
        <p className="flex items-center gap-1 text-xs">
          <MonitorUp className="h-3.5 w-3.5" aria-hidden />
          No audio. You can stop sharing at any time using your browser controls.
        </p>
      </CardContent>
    </Card>
  );
}

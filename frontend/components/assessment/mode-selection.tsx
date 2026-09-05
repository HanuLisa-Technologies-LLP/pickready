"use client";

// The assessment mode selection screen (dual-mode spec section 2).
//
// Two cards, one choice, made BEFORE consent and before anything starts.
// Nothing here opens a device or creates a session: choosing a mode only
// tells the server which consent terms to show next. The choice freezes the
// moment the assessment begins, and the server, not this screen, enforces
// that; a frozen state renders the chosen card as the only path forward.

import * as React from "react";
import { MessageSquareText, Video } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssessmentMode } from "@/lib/types";

export const MODE_TITLE = "Choose how you want to take this assessment";

const MODES: Array<{
  mode: AssessmentMode;
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  points: string[];
  action: string;
}> = [
  {
    mode: "conversational",
    title: "Conversational assessment",
    icon: MessageSquareText,
    points: [
      "You answer in writing, one question at a time.",
      "The interviewer follows up on what you say.",
      "Some questions may be multiple choice or coding exercises.",
    ],
    action: "Continue with the conversation",
  },
  {
    mode: "video_interview",
    title: "Video interview",
    icon: Video,
    points: [
      "You answer out loud, on camera, one question at a time.",
      "Your interview is recorded and transcribed afterwards.",
      "You move to the next question at your own pace.",
    ],
    action: "Continue with the video interview",
  },
];

export function ModeSelection({
  frozenMode,
  busy,
  onSelect,
}: {
  /** When the assessment has already begun, only this mode may continue. */
  frozenMode: AssessmentMode | null;
  busy: boolean;
  onSelect: (mode: AssessmentMode) => void;
}) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold">{MODE_TITLE}</h1>
      <p className="text-sm leading-6">
        Both modes are assessed the same way, on the same criteria. Pick the
        one you are most comfortable in. You will see the consent terms for
        your choice before anything begins.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {MODES.map(({ mode, title, icon: Icon, points, action }) => {
          const disabled = busy || (frozenMode !== null && frozenMode !== mode);
          return (
            <Card key={mode} className={disabled && frozenMode !== mode ? "opacity-60" : undefined}>
              <CardHeader className="flex flex-row items-center gap-3 space-y-0">
                {/* Navy: structure, the frame of a choice. */}
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-navy-600 text-white">
                  <Icon className="h-5 w-5" />
                </span>
                <CardTitle className="text-base">{title}</CardTitle>
              </CardHeader>
              <CardContent className="flex h-full flex-col gap-4">
                <ul className="space-y-2">
                  {points.map((point) => (
                    <li key={point} className="flex gap-3 text-sm leading-6">
                      <span aria-hidden className="mt-2 h-1 w-4 shrink-0 bg-teal-600" />
                      <span>{point}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-auto">
                  <Button
                    className="w-full"
                    disabled={disabled}
                    onClick={() => onSelect(mode)}
                  >
                    {action}
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      {frozenMode !== null ? (
        <p className="text-sm leading-6">
          This assessment has already begun in one mode, so the other is no
          longer available.
        </p>
      ) : null}
    </div>
  );
}

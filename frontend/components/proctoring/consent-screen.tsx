"use client";

// The consent screen (proctoring spec 8.1).
//
// A LEGAL REQUIREMENT, NOT A FORMALITY. The seven statements below are the
// specification's own content, and they are the whole of what a candidate is
// told before the camera and microphone are opened. They are exported as a
// constant so the test asserts the words a candidate reads rather than a
// paraphrase of them, and so a change to the notice is a change to one array
// in one file.
//
// The button is the explicit action. Nothing on this screen opens a device,
// starts a check or creates a session: the candidate reads first, agrees
// second, and the browser asks for the camera only after that.

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const CONSENT_TITLE = "This assessment is monitored";

export const CONSENT_POINTS = [
  "This assessment is monitored.",
  "Your camera and microphone will be on for the whole assessment.",
  "No video or audio is recorded or stored. The system only notes when something unusual happens.",
  "Copy-paste, right-click, and developer tools are disabled.",
  "The assessment runs in fullscreen and must stay in fullscreen.",
  "You will be warned if something unusual is detected. After three warnings the assessment may end.",
  "A summary of anything detected is shared with the employer.",
] as const;

export const CONSENT_ACTION = "I understand and agree";

export function ConsentScreen({ onAgree }: { onAgree: () => void }) {
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>{CONSENT_TITLE}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <ul className="space-y-3">
            {CONSENT_POINTS.map((point) => (
              <li key={point} className="flex gap-3 text-sm leading-6">
                {/* Teal, because this is the evidence of what was disclosed.
                    A rule and not a word, so nothing here reads as a grade. */}
                <span aria-hidden className="mt-2 h-1 w-4 shrink-0 bg-teal-600" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm leading-6">
            Agreeing records the time you agreed. The assessment cannot begin without it.
          </p>
          <Button size="lg" onClick={onAgree}>
            {CONSENT_ACTION}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

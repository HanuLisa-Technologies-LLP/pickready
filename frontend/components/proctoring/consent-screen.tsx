"use client";

// The consent screen (proctoring spec 8.1).
//
// A LEGAL REQUIREMENT, NOT A FORMALITY. The seven statements below are the
// whole of what a candidate is told before the camera and microphone are
// opened. They are exported as a constant so the test asserts the words a
// candidate reads rather than a paraphrase of them, and so a change to the
// notice is a change to one array in one file.
//
// THE THIRD STATEMENT WAS REVERSED ON 2026-09-22. It read:
//
//   "No video or audio is recorded or stored. The system only notes when
//    something unusual happens."
//
// The owner ruled that assessment media IS stored ("Media storage is
// required. The assessment video must be compressed and stored securely in
// S3"). Principle P5, the candidate is always informed, is locked and
// unchanged, and it is the reason this line could not simply be deleted: a
// screen that went quiet about recording after the product started recording
// would be worse than one that never mentioned it. So the statement now says
// what actually happens, in the same plain register as the other six, and it
// names the three things a person would want to know: that it is recorded,
// that it is protected, and who can see it.
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
  "Your assessment is recorded. The recording is encrypted, stored securely, and can be viewed by the hiring team for this role.",
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
              <li key={point} className="flex gap-3 text-sm">
                {/* Teal, because this is the evidence of what was disclosed.
                    A rule and not a word, so nothing here reads as a grade. */}
                <span aria-hidden className="mt-2 h-1 w-4 shrink-0 bg-teal-600" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
          <p className="text-sm">
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

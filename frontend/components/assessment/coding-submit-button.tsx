"use client";

// The Send control for a coding question, which is FINAL (assessment spec 5:
// "Submit: final and irreversible").
//
// Every other format sends on one press, because every other answer is
// quick to write and the next question follows at once. A coding answer is
// different twice over: it is the product of up to twenty minutes of work,
// and sending it is what makes the server run the hidden tests against it,
// after which nothing about it can change. So the press opens a confirmation
// naming the language being submitted (the commonest avoidable mistake is
// submitting in the wrong one) and saying plainly that there is no way back.
//
// It is a ConfirmButton, so it is an alertdialog that focuses Keep working
// first and does not close on a stray click. Ctrl/Cmd+Enter inside the editor
// runs the sample tests instead of sending (see `coding-answer.tsx`), so an
// irreversible act is never one chord away from the keys a candidate is
// typing with.

import { Loader2, Send } from "lucide-react";

import { ConfirmButton } from "@/components/confirm-button";
import { languageLabel } from "@/lib/assessment/answers";

export function CodingSubmitButton({
  language,
  disabled,
  sending,
  onConfirm,
}: {
  /** The language of the answer being sent. */
  language: string;
  disabled: boolean;
  sending: boolean;
  onConfirm: () => void;
}) {
  return (
    <ConfirmButton
      size="lg"
      disabled={disabled || sending}
      destructive={false}
      title="Submit your code as your final answer?"
      description={
        <>
          You are submitting your {languageLabel(language)} code. Once it is submitted you cannot
          change it or come back to this question. It will then be checked against further tests
          that you have not seen.
        </>
      }
      cancelLabel="Keep working"
      confirmLabel="Submit final answer"
      onConfirm={onConfirm}
      data-testid="coding-submit"
    >
      {sending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Send className="h-4 w-4" aria-hidden="true" />
      )}
      {sending ? "Submitting" : "Submit final answer"}
    </ConfirmButton>
  );
}

"use client";

// The security check every sign-in, registration and password screen asks for.
//
// THE SERVER DRAWS AND JUDGES IT. This component shows the image the server
// drew (`POST /auth/captcha/challenge`), sends the typed characters back
// (`POST /auth/captcha/verify`) and hands its parent the single-use PROOF the
// server returns. The parent sends that proof with the request that does the
// real work (the session exchange, a security code request, an invitation
// setup), and it is that request, on the server, that spends it. Nothing here
// compares an answer: a check the browser could pass by itself would be no
// check at all.
//
// A proof is spent once, and so is a challenge, so after every verification,
// right or wrong, a fresh image is loaded and the field is cleared.

import * as React from "react";
import { Loader2, RefreshCw } from "lucide-react";

import { ApiError, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type CaptchaPurpose =
  | "candidate_login"
  | "candidate_register"
  | "company_login"
  | "company_register"
  | "invite_join"
  | "provider_login"
  | "bd_login"
  | "password_change"
  | "password_reset";

type Challenge = { challenge_id: string; image: string; expires_in: number };

/** A refusal the person can act on, in the server's words where it gave any. */
export class CaptchaError extends Error {}

export const CAPTCHA_EMPTY_MESSAGE =
  "Type the characters shown in the security check.";
const CAPTCHA_LOAD_MESSAGE =
  "The security check could not load. Choose Show new characters to try again.";

export interface CaptchaHandle {
  /** Verify what was typed and return a single-use proof, or throw a
   *  `CaptchaError` carrying the sentence to show. Loads a new image either
   *  way, because the one just answered is spent. */
  prove: () => Promise<string>;
  /** True once something has been typed, for a caller that must decide
   *  before an await (a sign-in popup has to open on the click itself). */
  hasAnswer: () => boolean;
  /** A fresh image and an empty field. */
  reset: () => void;
}

function serverSentence(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return "Too many attempts. Please wait a minute and try again.";
    }
    const detail =
      error.detail && typeof error.detail === "object" && "detail" in error.detail
        ? (error.detail as { detail: unknown }).detail
        : undefined;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
}

export const Captcha = React.forwardRef<
  CaptchaHandle,
  { purpose: CaptchaPurpose; disabled?: boolean; idPrefix?: string; className?: string }
>(function Captcha({ purpose, disabled, idPrefix = "captcha", className }, ref) {
  const [challenge, setChallenge] = React.useState<Challenge | null>(null);
  const [answer, setAnswer] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const answerRef = React.useRef("");
  const challengeRef = React.useRef<Challenge | null>(null);
  const request = React.useRef(0);
  const fieldId = `${idPrefix}-answer`;

  const load = React.useCallback(async () => {
    const mine = ++request.current;
    setLoading(true);
    setLoadError(null);
    setAnswer("");
    answerRef.current = "";
    try {
      const next = await apiPost<Challenge>("/auth/captcha/challenge", { purpose });
      if (mine !== request.current) return;
      challengeRef.current = next;
      setChallenge(next);
    } catch (error) {
      if (mine !== request.current) return;
      challengeRef.current = null;
      setChallenge(null);
      setLoadError(serverSentence(error, CAPTCHA_LOAD_MESSAGE));
    } finally {
      if (mine === request.current) setLoading(false);
    }
  }, [purpose]);

  React.useEffect(() => {
    void load();
  }, [load]);

  React.useImperativeHandle(
    ref,
    () => ({
      hasAnswer: () => answerRef.current.trim().length > 0,
      reset: () => void load(),
      prove: async () => {
        const typed = answerRef.current.trim();
        const current = challengeRef.current;
        if (!current) throw new CaptchaError(CAPTCHA_LOAD_MESSAGE);
        if (!typed) throw new CaptchaError(CAPTCHA_EMPTY_MESSAGE);
        try {
          const result = await apiPost<{ captcha_proof: string }>(
            "/auth/captcha/verify",
            { challenge_id: current.challenge_id, answer: typed, purpose }
          );
          return result.captcha_proof;
        } catch (error) {
          throw new CaptchaError(
            serverSentence(error, "The security check did not go through. Try the new image.")
          );
        } finally {
          void load();
        }
      },
    }),
    [load, purpose]
  );

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={fieldId}>Security check</Label>
      <div className="flex items-center gap-2">
        <div className="flex h-16 w-[200px] shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-surface">
          {challenge && !loading ? (
            // A data URI the server drew, so there is nothing for the image
            // optimiser to fetch or resize.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={challenge.image}
              alt="Security check characters. Type them in the field below."
              width={200}
              height={64}
              className="h-16 w-[200px]"
              draggable={false}
            />
          ) : loading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={disabled || loading}
          aria-label="Show new characters"
          title="Show new characters"
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-surface transition-colors duration-150 hover:bg-brand-100/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <Input
        id={fieldId}
        value={answer}
        disabled={disabled || loading || !challenge}
        onChange={(event) => {
          setAnswer(event.target.value);
          answerRef.current = event.target.value;
        }}
        autoComplete="off"
        autoCapitalize="characters"
        autoCorrect="off"
        spellCheck={false}
        maxLength={12}
        placeholder="Type the characters"
        aria-describedby={loadError ? `${fieldId}-error` : undefined}
      />
      {loadError ? (
        <p id={`${fieldId}-error`} role="alert" className="text-sm font-medium text-destructive">
          {loadError}
        </p>
      ) : null}
    </div>
  );
});

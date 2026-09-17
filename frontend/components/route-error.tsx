"use client";

import * as React from "react";
import Link from "next/link";
import { AlertCircle, ArrowLeft, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The one recovery surface behind every `error.tsx` in the app.
 *
 * WHY THIS EXISTS. Before it there was no `error.tsx`, no `global-error.tsx`
 * and no `not-found.tsx` anywhere under `app/`, across all six route groups.
 * An uncaught render exception, a null dereference on an unexpected API shape,
 * a third party throwing, therefore blanked the whole page to Next's unstyled
 * default with no route back into the product except a hard reload. That is the
 * worst state this product can be in and it was the one state nobody had
 * designed.
 *
 * THREE RULES IT FOLLOWS.
 *
 * 1. IT NAMES THE SURFACE. "Something went wrong" tells a recruiter nothing and
 *    is the string the brief specifically calls out. Each boundary passes the
 *    thing that failed, so the message is "This job could not be opened" rather
 *    than a shrug. A person who knows WHICH part broke can route around it.
 *
 * 2. IT ALWAYS OFFERS A WAY OUT, and two of them. `reset()` re-renders the
 *    segment, which is the right first move for a transient fault and costs
 *    nothing. The second is a link somewhere that is known to work, because a
 *    retry that keeps failing is a trap, and the boundary cannot know whether
 *    the fault is transient.
 *
 * 3. IT SHOWS THE DIGEST AND NEVER THE STACK. Next hands a server error a
 *    `digest`, which is a HASH, not a trace: it carries no path, no SQL, no
 *    provider name and no candidate data, and it is the one token that lets
 *    support tie a user's screenshot to a server log line. The message and the
 *    stack are deliberately not rendered, because in production they are the
 *    two things that leak internals to whoever is looking at the screen.
 */
export function RouteError({
  /** What the user was trying to reach. Becomes the headline. */
  surface,
  /** One plain sentence about what to do next. */
  description,
  error,
  reset,
  /** Where "out" goes. Defaults to the product root. */
  backHref = "/",
  backLabel = "Go back",
  className,
}: {
  surface: string;
  description?: string;
  error: Error & { digest?: string };
  reset: () => void;
  backHref?: string;
  backLabel?: string;
  className?: string;
}) {
  // Development only. In production this would be the one place a stack could
  // reach a browser console on a shared screen, and it buys nothing: the server
  // already logged the same throw with the same digest.
  React.useEffect(() => {
    if (process.env.NODE_ENV === "development") {
      console.error("[route-error]", surface, error);
    }
  }, [error, surface]);

  return (
    <div
      role="alert"
      className={cn(
        "mx-auto flex min-h-[60vh] w-full max-w-xl flex-col items-center justify-center px-6 py-16 text-center",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="grid h-12 w-12 place-items-center border border-destructive/40 bg-destructive/10 text-destructive"
      >
        <AlertCircle className="h-6 w-6" />
      </span>

      <h1 className="mt-5 text-xl font-semibold tracking-tight">{surface}</h1>

      <p className="mt-2 text-pretty text-sm leading-6">
        {description ??
          "This section did not load. It is usually temporary, so trying again is the quickest fix."}
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Button onClick={reset} className="gap-2">
          <RotateCw className="h-4 w-4" aria-hidden="true" />
          Try again
        </Button>
        <Button asChild variant="outline" className="gap-2">
          <Link href={backHref}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {backLabel}
          </Link>
        </Button>
      </div>

      {error.digest ? (
        <p className="mt-6 text-xs">
          Quote this reference if you contact support
          <span className="mt-1 block select-all font-mono text-xs tracking-wider">
            {error.digest}
          </span>
        </p>
      ) : null}
    </div>
  );
}

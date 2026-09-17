"use client";

// The ReadyPick boundary. Next mounts this in place of the segment when a
// render below it throws, so the portal frame survives and the user keeps a
// route out. Copy is specific to this portal on purpose: "Something went
// wrong" tells a person nothing about what is safe and what is lost.
//
// See components/route-error.tsx for why the digest is rendered and the stack
// is not.

import { RouteError } from "@/components/route-error";

export default function PublicError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This page could not be loaded"
      description="Something interrupted this page. Trying again usually resolves it."
      error={error}
      reset={reset}
      backHref="/"
      backLabel="Back to home"
    />
  );
}

"use client";

// The Candidate Portal boundary. Next mounts this in place of the segment when a
// render below it throws, so the portal frame survives and the user keeps a
// route out. Copy is specific to this portal on purpose: "Something went
// wrong" tells a person nothing about what is safe and what is lost.
//
// See components/route-error.tsx for why the digest is rendered and the stack
// is not.

import { RouteError } from "@/components/route-error";

export default function CandidateError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This page could not be opened"
      description="Nothing you have submitted has been lost. Try again, or go back to your jobs."
      error={error}
      reset={reset}
      backHref="/portal"
      backLabel="Back to my jobs"
    />
  );
}

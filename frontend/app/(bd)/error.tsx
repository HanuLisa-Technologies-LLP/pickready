"use client";

// The Business Development boundary. Next mounts this in place of the segment when a
// render below it throws, so the portal frame survives and the user keeps a
// route out. Copy is specific to this portal on purpose: "Something went
// wrong" tells a person nothing about what is safe and what is lost.
//
// See components/route-error.tsx for why the digest is rendered and the stack
// is not.

import { RouteError } from "@/components/route-error";

export default function BusinessDevelopmentError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This page could not be opened"
      description="No lead has been changed. Try again, or go back to your leads."
      error={error}
      reset={reset}
      backHref="/bd"
      backLabel="Back to leads"
    />
  );
}

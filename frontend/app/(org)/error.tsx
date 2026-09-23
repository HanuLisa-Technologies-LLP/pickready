"use client";

// The Customer Portal boundary. Next mounts this in place of the segment when a
// render below it throws, so the portal frame survives and the user keeps a
// route out. Copy is specific to this portal on purpose: "Something went
// wrong" tells a person nothing about what is safe and what is lost.
//
// See components/route-error.tsx for why the digest is rendered and the stack
// is not.

import { RouteError } from "@/components/route-error";

export default function OrgError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This workspace could not be opened"
      description="Your jobs and candidates are safe. This is usually a temporary fault, so trying again is the quickest fix."
      error={error}
      reset={reset}
      backHref="/org/jobs"
      backLabel="Back to jobs"
    />
  );
}

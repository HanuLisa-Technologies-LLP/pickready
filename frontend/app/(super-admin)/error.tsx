"use client";

// The Provider Portal boundary. Next mounts this in place of the segment when a
// render below it throws, so the portal frame survives and the user keeps a
// route out. Copy is specific to this portal on purpose: "Something went
// wrong" tells a person nothing about what is safe and what is lost.
//
// See components/route-error.tsx for why the digest is rendered and the stack
// is not.

import { RouteError } from "@/components/route-error";

export default function ProviderError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This console could not be opened"
      description="No customer record has been changed. Try again, or go back to customers."
      error={error}
      reset={reset}
      backHref="/admin"
      backLabel="Back to customers"
    />
  );
}

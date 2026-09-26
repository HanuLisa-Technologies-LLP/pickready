"use client";

// The root boundary. Catches anything thrown outside the four portal groups,
// which in practice is the standalone routes: /apply, /assessments/invite,
// /verify-employment and the landing page.

import { RouteError } from "@/components/route-error";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <RouteError
      surface="This page could not be loaded"
      description="Something interrupted this page. Trying again usually resolves it, and nothing you have entered has been submitted."
      error={error}
      reset={reset}
      backHref="/"
      backLabel="Back to home"
    />
  );
}

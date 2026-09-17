import type { Metadata } from "next";
import Link from "next/link";
import { FileQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * The 404. There was none, so a mistyped or expired URL rendered Next's
 * unstyled default, which reads as the product being broken rather than as the
 * address being wrong.
 *
 * It deliberately offers BOTH doors. This app serves four portals plus
 * candidates, and a 404 is exactly the moment the visitor's role is unknown:
 * sending everyone to one workspace would be wrong for most of them, and
 * guessing from a cookie here would be a routing decision made on the error
 * path. Home and sign in are the two destinations that are correct for
 * everybody.
 */
export const metadata: Metadata = {
  title: "Page not found",
  // A 404 carries no content worth indexing and an indexed one competes with
  // the real pages.
  robots: { index: false, follow: false },
};

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col items-center justify-center px-6 py-16 text-center">
      <span
        aria-hidden="true"
        className="grid h-12 w-12 place-items-center border border-border bg-navy-50 text-navy-600"
      >
        <FileQuestion className="h-6 w-6" />
      </span>

      <h1 className="mt-5 text-xl font-semibold tracking-tight">
        This page does not exist
      </h1>

      <p className="mt-2 text-pretty text-sm leading-6">
        The address may be mistyped, or the link may have expired. Assessment
        and verification links are single use and stop working once they are
        finished.
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <Button asChild>
          <Link href="/">Back to home</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/login">Sign in</Link>
        </Button>
      </div>
    </div>
  );
}

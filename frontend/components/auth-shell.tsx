import * as React from "react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { Logo } from "@/components/brand";
import { LogomarkHero } from "@/components/brand/logomark-hero";
import { DotPattern } from "@/components/magicui";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The shell every authentication screen sits in: sign in, create account, and
 * accepting a team invitation.
 *
 * It is deliberately quiet. One centred card on the canvas, the brand lockup
 * above it, and a very low-contrast dot field behind. The lockup already
 * contains its own wordmark, so no text "ReadyPick" is rendered beside it.
 *
 * A Server Component, so a page can render the frame without the sign-in form's
 * client boundary reaching the whole route.
 */
export function AuthShell({
  title,
  description,
  children,
  footer,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-canvas px-4 py-10">
      <DotPattern
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full text-brand-600/25 [mask-image:radial-gradient(420px_circle_at_center,white,transparent)]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-28 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full bg-brand-500/20 blur-3xl"
      />

      <div className={cn("relative z-10 w-full max-w-md", className)}>
        {/* THE SIGNATURE MOMENT. spec-doc5 §C.2 allows the Three.js logomark
            on the landing and login surfaces and nowhere else, and
            `tests/logomark-placement.test.ts` counts the call sites rather than
            trusting this comment.

            It degrades to the flat lockup on reduced motion, without WebGL, and
            during server rendering -- so the wordmark below it is the thing
            that always renders and the scene is the thing that sometimes
            does. */}
        <div className="mb-2 flex justify-center">
          <LogomarkHero size={132} />
        </div>
        <div className="mb-7 flex justify-center">
          <Logo variant="full" height={36} href="/" priority />
        </div>

        <Card className="shadow-pop">
          <CardContent className="space-y-6 p-6 sm:p-8">
            <div className="space-y-2 text-center">
              <h1 className="text-balance text-xl font-bold tracking-tight">
                {title}
              </h1>
              {description ? (
                <p className="text-pretty text-sm leading-6">{description}</p>
              ) : null}
            </div>
            {children}
          </CardContent>
        </Card>

        {footer ? (
          <div className="mt-6 text-center text-sm leading-6">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}

/** The "or" rule between a social button and an email form. */
export function AuthDivider({ label = "or" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-px flex-1 bg-border" />
      <span className="text-xs font-medium uppercase tracking-[0.12em] opacity-70">
        {label}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/** A text link inside auth copy, styled once so all three screens match. */
export function AuthLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="font-semibold text-brand-600 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      {children}
    </Link>
  );
}

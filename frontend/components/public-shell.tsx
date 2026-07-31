import * as React from "react";

import { cn } from "@/lib/utils";
import { Logo } from "@/components/brand";

/**
 * The frame for pages a person reaches without an account: the public job
 * application, the employer verification form, and the tokenized outreach page.
 *
 * These are first impressions, so they carry the same header treatment as the
 * marketing site: the brand lockup on a glass bar, a canvas background, and a
 * single centred column. It is a Server Component, so a page keeps its client
 * boundary on the interactive part rather than the chrome.
 */
export function PublicShell({
  children,
  width = "reading",
  className,
}: {
  children: React.ReactNode;
  /** `reading` is the 768px form column, `wide` the 1024px two-column layout. */
  width?: "reading" | "wide";
  className?: string;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-canvas text-foreground">
      <header className="glass sticky top-0 z-40 border-b border-border">
        <div
          className={cn(
            "mx-auto flex h-16 items-center px-6",
            width === "wide" ? "max-w-5xl" : "max-w-3xl"
          )}
        >
          <Logo variant="full" height={28} href="/" priority />
        </div>
      </header>

      <main
        className={cn(
          "mx-auto w-full flex-1 px-6 py-8 sm:py-12",
          width === "wide" ? "max-w-5xl" : "max-w-3xl",
          className
        )}
      >
        {children}
      </main>

      <footer className="border-t border-border">
        <div
          className={cn(
            "mx-auto flex flex-wrap items-center justify-between gap-2 px-6 py-6 text-xs",
            width === "wide" ? "max-w-5xl" : "max-w-3xl"
          )}
        >
          <span>Powered by PickReady</span>
          <span className="opacity-80">
            Your details are shared only with the hiring team for this role.
          </span>
        </div>
      </footer>
    </div>
  );
}

/**
 * A centred single-message page: link expired, already applied, thank you.
 * Used for every terminal state on a public route so they all look alike.
 */
export function PublicNotice({
  icon,
  tone = "neutral",
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  /** `success` tints the icon tile green, `error` red, `neutral` brand. */
  tone?: "neutral" | "success" | "error";
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const tile =
    tone === "success"
      ? "bg-rating-1-bg text-rating-1"
      : tone === "error"
        ? "bg-destructive/10 text-destructive"
        : "bg-brand-100 text-accent-foreground";

  return (
    <PublicShell>
      <div className="mx-auto flex max-w-md flex-col items-center rounded-2xl border border-border bg-surface px-6 py-12 text-center shadow-card">
        {icon ? (
          <span className={cn("grid h-14 w-14 place-items-center rounded-2xl", tile)}>
            {icon}
          </span>
        ) : null}
        <h1 className="mt-5 text-balance text-lg font-bold tracking-tight">
          {title}
        </h1>
        {description ? (
          <p className="mt-2 text-pretty text-sm leading-6">{description}</p>
        ) : null}
        {action ? <div className="mt-6">{action}</div> : null}
      </div>
    </PublicShell>
  );
}

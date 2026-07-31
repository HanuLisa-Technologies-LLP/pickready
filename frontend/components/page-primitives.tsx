import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { AlertCircle, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shared page-state primitives (docs/spec/DESIGN_BRIEF.md, 2026-07-28).
 *
 * Every in-app screen renders one of four states: loading, error, empty, or
 * content. Before this file each page invented its own, so a spinner on one
 * screen was a bare "Loading..." on the next. These are Server Components (no
 * `"use client"`), so a page can render them without pulling a client boundary
 * up the tree.
 *
 * House rules baked in here so no page has to remember them:
 *  - text is never grey, every string below inherits `--ink`;
 *  - no em dashes;
 *  - no numbers are rendered for anything rated.
 */

/* -------------------------------------------------------------------------- */
/*  Section                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * A titled card section. The single container style for app content: surface
 * card, 24px padding, soft border, never glass (glass is nav only, rule 7).
 */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  contentClassName,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <Card className={cn("shadow-card", className)}>
      {title || actions || description ? (
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div className="min-w-0 space-y-1">
            <CardTitle className="text-base">{title}</CardTitle>
            {description ? (
              <p className="text-sm leading-6">{description}</p>
            ) : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </CardHeader>
      ) : null}
      <CardContent className={cn(!title && !actions && "pt-6", contentClassName)}>
        {children}
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Empty                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * The one empty state. An icon in a brand-tinted tile, a short title, one line
 * of plain guidance, and at most one action.
 */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 py-14 text-center",
        className
      )}
    >
      <span className="grid h-12 w-12 place-items-center rounded-xl bg-brand-100 text-accent-foreground">
        <Icon className="h-6 w-6" aria-hidden="true" />
      </span>
      <p className="mt-4 text-base font-semibold">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-pretty text-sm leading-6">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Error                                                                      */
/* -------------------------------------------------------------------------- */

/** The one error state. Destructive is a border and an icon, never grey text. */
export function ErrorState({
  title = "Something went wrong",
  description,
  action,
  className,
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-destructive/40 bg-destructive/5 px-6 py-12 text-center",
        className
      )}
    >
      <span className="grid h-12 w-12 place-items-center rounded-xl bg-destructive/10 text-destructive">
        <AlertCircle className="h-6 w-6" aria-hidden="true" />
      </span>
      <p className="mt-4 text-base font-semibold">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-pretty text-sm leading-6">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

/** A one-line inline error, for a form or a panel that already has a frame. */
export function InlineError({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  if (!children) return null;
  return (
    <p
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm leading-6",
        className
      )}
    >
      <AlertCircle
        className="mt-1 h-4 w-4 shrink-0 text-destructive"
        aria-hidden="true"
      />
      <span>{children}</span>
    </p>
  );
}

/* -------------------------------------------------------------------------- */
/*  Loading                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Skeleton rows, sized like the content they stand in for. Always announce it,
 * a screen reader gets "Loading" while a sighted user gets the shape.
 */
export function LoadingRows({
  rows = 4,
  className,
  label = "Loading",
}: {
  rows?: number;
  className?: string;
  label?: string;
}) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className={cn("space-y-3", className)}
    >
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-14 w-full rounded-xl" />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

/** Skeleton cards, for a grid of tiles rather than a list of rows. */
export function LoadingCards({
  count = 3,
  className,
  label = "Loading",
}: {
  count?: number;
  className?: string;
  label?: string;
}) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className={cn("grid gap-4 sm:grid-cols-2 lg:grid-cols-3", className)}
    >
      {Array.from({ length: count }).map((_, index) => (
        <Skeleton key={index} className="h-32 w-full rounded-xl" />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Field                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * A labelled form row. One vertical rhythm for every form in the product:
 * label, control, then an optional single line of help. Help text is ink like
 * everything else, it is separated by size and not by colour.
 */
export function Field({
  label,
  htmlFor,
  hint,
  required,
  children,
  className,
}: {
  label: React.ReactNode;
  htmlFor?: string;
  hint?: React.ReactNode;
  required?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label
        htmlFor={htmlFor}
        className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed"
      >
        {label}
        {required ? (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {children}
      {hint ? <p className="text-xs leading-5 opacity-80">{hint}</p> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Definition list                                                            */
/* -------------------------------------------------------------------------- */

/** A read-only label/value pair, used across detail panels and profiles. */
export function DetailItem({
  label,
  children,
  className,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 space-y-1", className)}>
      <dt className="text-xs font-medium uppercase tracking-[0.08em] opacity-70">
        {label}
      </dt>
      <dd className="break-words text-sm leading-6">{children}</dd>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Responsive table shell                                                     */
/* -------------------------------------------------------------------------- */

/**
 * `<Table>` already frames and scrolls itself, so the responsive pattern is:
 * wrap the table in `hidden md:block`, and render a `<RowCard>` per row inside
 * a `space-y-3 md:hidden` list underneath. That keeps rule 8 (the page body
 * never scrolls horizontally) true from 360px up.
 */

/**
 * The stacked-card form a table takes under `md`. One card per row, each field
 * a label above its value.
 */
export function RowCard({
  title,
  meta,
  actions,
  children,
  className,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface p-4 shadow-card",
        className
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{title}</p>
          {meta ? <div className="mt-1 text-xs leading-5">{meta}</div> : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        ) : null}
      </div>
      {children ? <div className="mt-3 space-y-2">{children}</div> : null}
    </div>
  );
}

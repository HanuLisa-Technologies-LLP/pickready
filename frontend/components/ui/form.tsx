"use client";

// Minimal form-field helpers in shadcn spirit (label + control + error text)
// without pulling react-hook-form.

import * as React from "react";

import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";

/**
 * A labelled control with its hint and its error.
 *
 * THE FIELD OWNS THE WIRING, NOT THE CALLER. Before this, `error` rendered a
 * bare paragraph beside the control: visually obvious, and invisible to a
 * screen reader, which reads a field and its label and never reaches a
 * sibling `<p>`. Every caller would have had to remember `aria-invalid`,
 * `aria-describedby` and a matching id, and none of them did. So the
 * association is made HERE, once, by cloning the control: a caller that keeps
 * using `FormField` cannot get it wrong, and a caller that already sets one of
 * these attributes keeps its own value.
 *
 * The error is `role="alert"`, so it is announced when it appears rather than
 * only when focus happens to land back on the field.
 */
export function FormField({
  label,
  htmlFor,
  required,
  error,
  hint,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  error?: string | null;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const generated = React.useId();
  const base = htmlFor ?? generated;
  const hintId = `${base}-hint`;
  const errorId = `${base}-error`;
  const showHint = Boolean(hint) && !error;

  // Only a single element child can carry the wiring. A fragment or a group of
  // controls (a pair of radios, a min/max row) is left alone rather than
  // silently annotating whichever element happens to come first.
  const control = React.isValidElement(children)
    ? React.cloneElement(
        children as React.ReactElement<Record<string, unknown>>,
        describe(children as React.ReactElement<Record<string, unknown>>, {
          error: Boolean(error),
          required: Boolean(required),
          describedBy: [showHint ? hintId : null, error ? errorId : null]
            .filter(Boolean)
            .join(" "),
        })
      )
    : children;

  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={htmlFor}>
        {label}
        {required ? (
          <>
            <span aria-hidden="true" className="ml-0.5 text-muted-foreground">
              *
            </span>
            <span className="sr-only"> (required)</span>
          </>
        ) : null}
      </Label>
      {control}
      {showHint ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p
          id={errorId}
          role="alert"
          className="text-xs font-medium text-destructive"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The aria props to merge onto the control, with anything the caller already
 * set left exactly as it is. An explicit `aria-describedby` on the child is
 * EXTENDED rather than replaced: `register-flow` points its password box at a
 * requirements checklist, and clobbering that to show an error would trade one
 * announcement for another.
 */
function describe(
  child: React.ReactElement<Record<string, unknown>>,
  {
    error,
    required,
    describedBy,
  }: { error: boolean; required: boolean; describedBy: string }
): Record<string, unknown> {
  const props = child.props;
  const existing =
    typeof props["aria-describedby"] === "string"
      ? (props["aria-describedby"] as string)
      : "";
  const merged = [existing, describedBy].filter(Boolean).join(" ");

  const next: Record<string, unknown> = {};
  if (merged) next["aria-describedby"] = merged;
  if (error && props["aria-invalid"] === undefined) next["aria-invalid"] = true;
  if (required && props["aria-required"] === undefined && props.required === undefined) {
    next["aria-required"] = true;
  }
  return next;
}

export function FormSection({
  title,
  description,
  className,
  children,
}: {
  title: string;
  description?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("space-y-4", className)}>
      <div>
        <h3 className="text-base font-semibold">{title}</h3>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

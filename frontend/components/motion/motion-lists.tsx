"use client";

import * as React from "react";
import { AnimatePresence, motion, type Transition } from "framer-motion";

import { cn } from "@/lib/utils";
import { EASE } from "./motion-primitives";
import { usePrefersReducedMotion } from "./use-reduced-motion";

/**
 * Motion for content that ARRIVES AND LEAVES, which the product had none of.
 *
 * The existing wrappers in `motion-primitives.tsx` all animate a mount. Nothing
 * animated an unmount, so a row that was filtered out, a candidate moved to
 * another stage, or a panel that collapsed simply vanished between two frames.
 * A thing that disappears instantly reads as a rendering fault; a thing that
 * leaves reads as a consequence of what you just did, which is the only reason
 * to spend motion on it at all.
 *
 * THE EXIT IS FASTER THAN THE ENTRANCE, and deliberately so. An entrance is
 * showing you something you need to read, so it can take its time. An exit is
 * confirming something you already decided, and a slow one just delays the next
 * thing. Entrance 0.2s, exit 0.13s.
 *
 * NO OVERSHOOT ANYWHERE IN THIS FILE. DESIGN.md section 7 rules out spring
 * overshoot on anything a person is waiting on, and these wrap candidate rows
 * and assessment panels, which is precisely that. Everything here is the one
 * shared `EASE` curve.
 */

const ENTER: Transition = { duration: 0.2, ease: EASE };
const EXIT: Transition = { duration: 0.13, ease: EASE };

/**
 * A list whose items animate in and out as the underlying data changes.
 *
 * Each child MUST carry a stable `key` that identifies the row, not its index.
 * With an index key React reuses the element for a different row and the
 * animation plays on the wrong one, which looks like the list shuffling itself.
 *
 * ```tsx
 * <AnimatedList as="ul" className="space-y-2">
 *   {rows.map((row) => (
 *     <AnimatedListItem as="li" key={row.id}>...</AnimatedListItem>
 *   ))}
 * </AnimatedList>
 * ```
 */
export function AnimatedList({
  children,
  className,
  as: Component = "div",
}: {
  children: React.ReactNode;
  className?: string;
  as?: "div" | "ul" | "ol" | "tbody";
}) {
  const reduced = usePrefersReducedMotion();

  if (reduced) {
    return <Component className={className}>{children}</Component>;
  }

  // `initial={false}` stops the whole list playing an entrance on first paint.
  // Without it every candidate row animates in on page load, which is 25 rows
  // of movement the recruiter did not ask for and has to wait through before
  // the table is readable.
  return (
    <Component className={className}>
      <AnimatePresence initial={false}>{children}</AnimatePresence>
    </Component>
  );
}

/**
 * One row of an `AnimatedList`. Has no effect outside one, because
 * `AnimatePresence` is what keeps the element mounted long enough to exit.
 */
export function AnimatedListItem({
  children,
  className,
  as = "div",
}: {
  children: React.ReactNode;
  className?: string;
  as?: "div" | "li" | "tr";
}) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];

  if (reduced) {
    const Plain = as;
    return <Plain className={className}>{children}</Plain>;
  }

  return (
    <Component
      className={className}
      layout="position"
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0, transition: ENTER }}
      exit={{ opacity: 0, y: -4, transition: EXIT }}
    >
      {children}
    </Component>
  );
}

/**
 * Progressive disclosure: a panel that expands and collapses in place.
 *
 * Animates HEIGHT from `auto`, which framer-motion measures for us. The naive
 * alternative, toggling `display`, snaps the page and moves whatever the reader
 * was looking at; the other naive alternative, a fixed max-height, either clips
 * long content or leaves a pause on short content while the transition runs
 * over empty space.
 *
 * `overflow-hidden` is not optional while the height is mid-transition, or the
 * content spills over whatever is below it.
 */
export function Collapse({
  open,
  children,
  className,
}: {
  open: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();

  if (reduced) {
    return open ? <div className={className}>{children}</div> : null;
  }

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          className={cn("overflow-hidden", className)}
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1, transition: ENTER }}
          exit={{ height: 0, opacity: 0, transition: EXIT }}
        >
          {children}
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

/**
 * Crossfades between mutually exclusive views that occupy the same slot: a tab
 * body, a wizard step, or a panel swapping between loading, error and content.
 *
 * `mode="wait"` makes the outgoing view finish leaving before the incoming one
 * starts. Running both at once cross-dissolves two different sets of words on
 * top of each other, which is unreadable for the whole overlap.
 *
 * `viewKey` must change when the content changes, or nothing animates.
 */
export function SwapView({
  viewKey,
  children,
  className,
}: {
  viewKey: string;
  children: React.ReactNode;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={viewKey}
        className={className}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1, transition: ENTER }}
        exit={{ opacity: 0, transition: EXIT }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

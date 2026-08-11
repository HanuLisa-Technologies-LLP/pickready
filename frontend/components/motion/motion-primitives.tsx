"use client";

import * as React from "react";
import { motion, type Transition, type Variants } from "framer-motion";

import { cn } from "@/lib/utils";
import { usePrefersReducedMotion } from "./use-reduced-motion";

/**
 * Shared motion wrappers.
 *
 * House rules, applied here once so no page has to remember them:
 *  - duration is 150 to 250 ms, never longer;
 *  - easing is a single shared curve;
 *  - `prefers-reduced-motion: reduce` collapses every one of these to a plain
 *    element with no transform, no opacity fade and no delay.
 *
 * These are client components. Import them around the smallest subtree that
 * needs to move rather than marking a whole page `"use client"`.
 */

/** The one easing curve the product uses. */
export const EASE = [0.22, 1, 0.36, 1] as const;

/** Distance, in pixels, that an entering element travels. */
const DEFAULT_OFFSET = 12;

export type MotionDirection = "up" | "down" | "left" | "right" | "none";

function offsetFor(direction: MotionDirection, distance: number) {
  switch (direction) {
    case "up":
      return { y: distance };
    case "down":
      return { y: -distance };
    case "left":
      return { x: distance };
    case "right":
      return { x: -distance };
    default:
      return {};
  }
}

interface BaseMotionProps {
  children: React.ReactNode;
  className?: string;
  /** Seconds. Clamped to the 0.15 to 0.25 s house range. */
  duration?: number;
  /** Seconds to wait before starting. Ignored under reduced motion. */
  delay?: number;
  /** Which way the element travels in from. Defaults to `up`. */
  direction?: MotionDirection;
  /** Travel distance in pixels. Defaults to 12. */
  distance?: number;
  /** Rendered element. Defaults to `div`. */
  as?: "div" | "section" | "li" | "ul" | "span" | "article";
}

function clampDuration(duration: number | undefined) {
  return Math.min(0.25, Math.max(0.15, duration ?? 0.2));
}

function transition(duration: number, delay: number): Transition {
  return { duration, delay, ease: EASE };
}

/**
 * FadeIn: a page or panel entrance. Fades and slides a short distance on mount.
 *
 * ```tsx
 * <FadeIn className="space-y-6">{children}</FadeIn>
 * ```
 */
export function FadeIn({
  children,
  className,
  duration,
  delay = 0,
  direction = "up",
  distance = DEFAULT_OFFSET,
  as = "div",
}: BaseMotionProps) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];
  const seconds = clampDuration(duration);

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <Component
      className={className}
      initial={{ opacity: 0, ...offsetFor(direction, distance) }}
      animate={{ opacity: 1, x: 0, y: 0 }}
      transition={transition(seconds, delay)}
    >
      {children}
    </Component>
  );
}

/**
 * Stagger: a list container. Wrap the list, then wrap each row in
 * `<StaggerItem>`. Children enter one after another, 40 ms apart.
 *
 * ```tsx
 * <Stagger as="ul" className="grid gap-4">
 *   {rows.map((row) => (
 *     <StaggerItem as="li" key={row.id}>...</StaggerItem>
 *   ))}
 * </Stagger>
 * ```
 */
export function Stagger({
  children,
  className,
  delay = 0,
  /** Seconds between consecutive children. Defaults to 0.04. */
  step = 0.04,
  as = "div",
}: Omit<BaseMotionProps, "direction" | "distance" | "duration"> & {
  step?: number;
}) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  const container: Variants = {
    hidden: {},
    show: { transition: { staggerChildren: step, delayChildren: delay } },
  };

  return (
    <Component
      className={className}
      variants={container}
      initial="hidden"
      animate="show"
    >
      {children}
    </Component>
  );
}

/** One child of a `<Stagger>`. Has no effect outside one. */
export function StaggerItem({
  children,
  className,
  duration,
  direction = "up",
  distance = DEFAULT_OFFSET,
  as = "div",
}: Omit<BaseMotionProps, "delay">) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];
  const seconds = clampDuration(duration);

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  const item: Variants = {
    hidden: { opacity: 0, ...offsetFor(direction, distance) },
    show: {
      opacity: 1,
      x: 0,
      y: 0,
      transition: { duration: seconds, ease: EASE },
    },
  };

  return (
    <Component className={className} variants={item}>
      {children}
    </Component>
  );
}

/**
 * Reveal: animates the first time the element scrolls into view, then stays.
 * Use for marketing sections. Do not use it inside a data table.
 *
 * ```tsx
 * <Reveal className="mt-16"><FeatureGrid /></Reveal>
 * ```
 */
export function Reveal({
  children,
  className,
  duration,
  delay = 0,
  direction = "up",
  distance = 16,
  /** Fraction of the element that must be visible. Defaults to 0.15. */
  amount = 0.15,
  as = "div",
}: BaseMotionProps & { amount?: number }) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];
  const seconds = clampDuration(duration);

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <Component
      className={className}
      initial={{ opacity: 0, ...offsetFor(direction, distance) }}
      whileInView={{ opacity: 1, x: 0, y: 0 }}
      viewport={{ once: true, amount }}
      transition={transition(seconds, delay)}
    >
      {children}
    </Component>
  );
}

/**
 * RevealStagger: `Stagger`, but triggered by scroll rather than by mount.
 *
 * This exists because the landing page had the two confused. `Stagger` and
 * `FadeIn` animate on MOUNT, which for a section below the fold means the
 * animation runs, finishes, and is over before the element is ever on screen.
 * The user then scrolls to a section that is simply static -- the motion was
 * paid for and never seen. Marketing sections want this one; an app panel that
 * really does appear on mount wants `Stagger`.
 */
export function RevealStagger({
  children,
  className,
  delay = 0,
  step = 0.06,
  amount = 0.15,
  as = "div",
}: Omit<BaseMotionProps, "direction" | "distance" | "duration"> & {
  step?: number;
  amount?: number;
}) {
  const reduced = usePrefersReducedMotion();
  const Component = motion[as];

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  const container: Variants = {
    hidden: {},
    show: { transition: { staggerChildren: step, delayChildren: delay } },
  };

  return (
    <Component
      className={className}
      variants={container}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount }}
    >
      {children}
    </Component>
  );
}

/**
 * Pressable: the tap half of a micro-interaction. Scales down a little while
 * the pointer is held, which is the cheapest way to make a CTA feel like a
 * physical control rather than a link with a background.
 *
 * Wrap the button, do not replace it -- the child keeps its own semantics,
 * focus ring and disabled handling.
 */
export function Pressable({
  children,
  className,
  /** How far it rises on hover, in pixels. 0 disables the lift. */
  lift = 1,
}: {
  children: React.ReactNode;
  className?: string;
  lift?: number;
}) {
  const reduced = usePrefersReducedMotion();

  // `inline-flex` keeps the wrapper the size of the button in a row layout.
  // `[&>*]:w-full` is what stops it changing the layout it was dropped into:
  // in a `flex-col` CTA stack the wrapper stretches to the container, and
  // without this the button inside would shrink to its text and the full-width
  // mobile CTA would quietly become a small one.
  const shape = cn("inline-flex [&>*]:w-full", className);

  if (reduced) {
    return <div className={shape}>{children}</div>;
  }

  return (
    <motion.div
      className={shape}
      whileHover={{ y: -lift }}
      whileTap={{ scale: 0.97 }}
      transition={{ duration: 0.15, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

/**
 * HoverLift: a card that rises very slightly under the pointer. Purely
 * decorative, so it is the first thing reduced motion removes.
 */
export function HoverLift({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <motion.div
      className={cn(className)}
      whileHover={{ y: -4 }}
      transition={{ duration: 0.18, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

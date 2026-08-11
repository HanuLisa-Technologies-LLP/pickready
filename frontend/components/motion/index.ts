/**
 * Shared motion wrappers. Import from here, never from the file directly:
 *
 *   import { FadeIn, Stagger, StaggerItem, Reveal, HoverLift } from "@/components/motion";
 *
 * All of them are client components that collapse to no motion under
 * `prefers-reduced-motion: reduce`.
 */
export {
  FadeIn,
  Stagger,
  StaggerItem,
  Reveal,
  RevealStagger,
  Pressable,
  HoverLift,
  EASE,
  type MotionDirection,
} from "./motion-primitives";
export { usePrefersReducedMotion } from "./use-reduced-motion";

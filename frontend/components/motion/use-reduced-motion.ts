"use client";

import * as React from "react";

/**
 * Tracks the user's `prefers-reduced-motion` setting.
 *
 * Returns `false` on the very first client render (and during SSR) so markup
 * matches between server and client; the effect then corrects it before paint.
 * Every wrapper in this directory collapses to no motion when this is true.
 */
export function usePrefersReducedMotion(): boolean {
  const [prefersReduced, setPrefersReduced] = React.useState(false);

  React.useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setPrefersReduced(query.matches);

    const onChange = (event: MediaQueryListEvent) =>
      setPrefersReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return prefersReduced;
}

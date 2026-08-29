"use client";

/**
 * The R+P logomark as a live Three.js hero. LANDING AND LOGIN ONLY.
 *
 * spec-doc5 §C.2: "Use it for the landing/login hero and nowhere else -- this
 * is a signature moment, not a UI pattern to repeat." That restriction is
 * enforced by `tests/logomark-placement.test.ts`, which greps the app for
 * imports of this file and fails on any surface outside the allowed list. A
 * comment saying "landing only" is a comment; a test that counts the call sites
 * is the rule.
 *
 * IT DEGRADES TO THE FLAT MARK, ALWAYS
 * --------------------------------------
 * Three things make the canvas not render, and all three are ordinary rather
 * than exceptional:
 *
 *   * `prefers-reduced-motion`. A rotating logo is exactly the kind of ambient
 *     motion that setting exists for, and honouring it is not optional.
 *   * No WebGL context -- an old machine, a locked-down corporate browser, a
 *     headless CI screenshot.
 *   * Server rendering, where there is no canvas at all.
 *
 * In every one of them the component renders the existing flat `Logo`, which
 * is the correct fallback rather than a placeholder: it is the actual brand
 * mark, and a visitor who never sees the 3D version has not missed anything
 * they needed. The 3D scene is a flourish on the one page that gets one.
 *
 * THE ANIMATION IS DRIVEN BY THE CALLER'S LOOP, NOT BY A TIMER INSIDE THE
 * SCENE. `sweepShared` returns a step function taking elapsed seconds, so
 * stopping the animation is not calling it. An animation owning its own
 * interval cannot be switched off from outside without being torn down, which
 * is what makes reduced-motion support an afterthought in most scenes.
 */
import { useEffect, useRef, useState } from "react";

import { Logo } from "@/components/brand/logo";
import { cn } from "@/lib/utils";

export interface LogomarkHeroProps {
  /** Rendered size in CSS pixels. The scene scales from it. */
  size?: number;
  className?: string;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function LogomarkHero({ size = 220, className }: LogomarkHeroProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  // Starts FALSE and is set true only once a context is actually live. The
  // other order -- assume 3D, fall back on failure -- flashes the canvas before
  // the fallback appears, which is worse than never showing it.
  const [live, setLive] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || prefersReducedMotion()) return;

    let disposed = false;
    let frame = 0;
    let cleanup: (() => void) | undefined;

    // Dynamic import so three.js is not in the bundle of any page that does not
    // render this. It is ~600KB, and every portal page would otherwise carry it
    // for a component none of them mount.
    void (async () => {
      let THREE: typeof import("three");
      let mark: typeof import("@/components/brand/logomark-3d");
      try {
        [THREE, mark] = await Promise.all([
          import("three"),
          import("@/components/brand/logomark-3d"),
        ]);
      } catch {
        return; // stay on the flat mark
      }
      if (disposed) return;

      let renderer: import("three").WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      } catch {
        // No WebGL context. An old machine or a locked-down browser, and the
        // flat mark is the right answer.
        return;
      }

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      renderer.setPixelRatio(dpr);
      renderer.setSize(size, size, false);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.appendChild(renderer.domElement);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
      camera.position.set(0, 0, 4.2);

      const group = mark.createLogomark({ height: 1.25 });
      scene.add(group);

      // A studio key plus a soft fill. Deliberately not an environment map: the
      // mark should read as printed ink under a light, not as a reflective
      // object, and a reflection is the thing that makes a 3D logo look like a
      // 2003 corporate intro.
      scene.add(new THREE.AmbientLight(0xffffff, 1.35));
      const key = new THREE.DirectionalLight(0xffffff, 1.6);
      key.position.set(2.5, 3, 4);
      scene.add(key);

      // The sweep light. It travels along the shared stroke -- the brand's one
      // geometric idea -- rather than orbiting the whole mark.
      const sweep = new THREE.PointLight(0xffffff, 6, 6, 2);
      scene.add(sweep);
      const step = mark.sweepShared(group, sweep, { height: 1.25 });

      const clock = new THREE.Clock();
      const render = () => {
        if (disposed) return;
        const elapsed = clock.getElapsedTime();
        step(elapsed);
        // A slow, small oscillation rather than a full rotation. The mark has
        // a front; spinning it past the back is a novelty, and DESIGN.md §1
        // rules out decorative motion.
        group.rotation.y = Math.sin(elapsed * 0.35) * 0.32;
        group.rotation.x = Math.sin(elapsed * 0.22) * 0.06;
        renderer.render(scene, camera);
        frame = requestAnimationFrame(render);
      };

      // Assemble once on load, then hand over to the idle loop.
      mark.assemble(group, 1);
      setLive(true);
      frame = requestAnimationFrame(render);

      cleanup = () => {
        cancelAnimationFrame(frame);
        mark.disposeLogomark(group);
        renderer.dispose();
        renderer.domElement.remove();
      };
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [size]);

  return (
    <div
      className={cn("relative grid place-items-center", className)}
      style={{ width: size, height: size }}
    >
      {/* The canvas mounts here. Empty until a context is live. */}
      <div ref={hostRef} aria-hidden="true" className="absolute inset-0 grid place-items-center" />
      {/* The fallback IS the brand mark, not a placeholder. It is hidden rather
          than unmounted once the scene is live, so a WebGL context loss mid-
          session leaves something on the page instead of a hole. */}
      <div className={cn("transition-opacity duration-500", live ? "opacity-0" : "opacity-100")}>
        <Logo variant="mark" height={Math.round(size * 0.55)} priority />
      </div>
      {/* The mark is decorative here; the product name is stated in text
          alongside it on every surface that uses this. */}
      <span className="sr-only">ReadyPick</span>
    </div>
  );
}

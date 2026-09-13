import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  // This route sits directly under the root layout (no (public) group
  // layout in between), so the root's "%s | ReadyPick" title template is
  // spelled out here rather than relied on.
  title: "Site Under Construction | ReadyPick",
  description: "ReadyPick is preparing something new. Check back soon.",
};

export default function LandingPage() {
  return (
    <div className="relative min-h-screen bg-canvas">
      {/* Faint structural grid, decorative only. */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0"
        style={{
          backgroundImage:
            "linear-gradient(to right, rgba(10,37,64,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(10,37,64,0.05) 1px, transparent 1px)",
          backgroundSize: "84px 84px",
        }}
      />

      <div className="relative mx-auto flex min-h-screen max-w-[1400px] flex-col p-3 sm:p-4">
        <header className="flex h-[68px] items-center border border-navy-200/60 bg-white px-5 sm:h-[78px] sm:px-9">
          <span className="text-2xl font-bold tracking-[-0.045em] text-ink sm:text-[28px]">
            Ready<span className="text-teal-700">Pick</span>
          </span>
        </header>

        <main className="relative flex flex-1 items-center justify-center overflow-hidden border border-t-0 border-navy-900 bg-navy-900">
          <div className="w-[min(980px,calc(100%-4rem))] px-6 py-20 text-center sm:py-24">
            <span className="mb-6 inline-block text-sm font-bold leading-tight text-teal-400">
              ReadyPick
            </span>

            <h1 className="text-balance text-[clamp(3.5rem,10vw,8.9rem)] font-bold leading-[0.98] tracking-[-0.09em] text-white">
              Ready<span className="text-teal-400">Pick</span>
            </h1>

            <p className="mt-6 text-balance text-[clamp(1.3rem,3vw,2.5rem)] font-semibold leading-[1.15] tracking-[-0.03em] text-white">
              The candidate intelligence platform
            </p>

            <div className="mt-14">
              <p className="text-balance text-[clamp(2.1rem,5vw,4rem)] font-semibold lowercase leading-[1.05] tracking-[-0.05em] text-white">
                site under construction
              </p>
              <span
                aria-hidden="true"
                className="mx-auto mt-7 block h-[3px] w-[72px] bg-teal-400"
              />
            </div>
          </div>

          <footer className="absolute bottom-5 left-1/2 -translate-x-1/2">
            {/* Not a labeled control by design: same weight, color and
                decoration as static footer text, entry point to the
                platform for those who already know it is there. */}
            <Link
              href="/login"
              className="text-sm font-semibold leading-none text-white no-underline"
            >
              Readypick
            </Link>
          </footer>
        </main>
      </div>
    </div>
  );
}

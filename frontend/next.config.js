/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep development and production output separate. `next dev` watches and
  // serves chunks from its output directory; a concurrent `next build` must
  // never overwrite those files (which otherwise causes errors such as
  // "Cannot find module './992.js'").
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  output: "standalone",
  // Disabled deliberately: React StrictMode double-invokes effects/renders in dev,
  // which desyncs the Firebase Auth popup operation's internal promise and throws
  // "INTERNAL ASSERTION FAILED: Pending promise was never set" during
  // signInWithPopup (Google sign-in). StrictMode is a dev-only check, so this has
  // NO production effect. Paired with the explicit popup resolver in lib/firebase.ts.
  reactStrictMode: false,

  // Barrel-file cost. `import { Users } from "lucide-react"` pulls the package's
  // index, which re-exports well over a thousand modules; the same is true of
  // date-fns and recharts. In development every one of those modules is compiled
  // and served individually, which is a large part of why a first visit to a
  // page takes seconds. This rewrites each barrel import to the single file it
  // actually needs. Behaviour is identical, only the module graph shrinks.
  experimental: {
    optimizePackageImports: [
      "lucide-react",
      "date-fns",
      "recharts",
      "framer-motion",
      "cmdk",
    ],
  },

  // Next's dev server throws away a compiled page 25 seconds after it stops
  // being requested and keeps only 2 pages in memory. In an app this size that
  // means normal clicking around recompiles pages that were compiled a minute
  // ago, and every one of those recompiles is the multi-second stall the client
  // is describing. Holding 25 pages for an hour trades some dev-server memory
  // for pages that stay compiled. Development only; production is prebuilt.
  onDemandEntries: {
    maxInactiveAge: 25 * 1000,
    pagesBufferLength: 2,
  },

  // NOTE: the same-origin API proxy is deliberately NOT a `rewrites()` entry.
  // Rewrites are resolved during `next build` and frozen into
  // routes-manifest.json, so a destination read from the environment would be
  // captured at build time and pin the image to one backend; an unset variable
  // at build time emits no rewrite at all and API calls fall through to a 404
  // page. It lives in app/api/[...path]/route.ts instead, which reads
  // BACKEND_INTERNAL_URL per request.

  // Never let a browser replay an HTML document from a previous dev-server run.
  // Next embeds the build id in every chunk URL, so a cached document served
  // after a restart asks for chunks that no longer exist and the page arrives
  // as dead HTML with no React attached, which looked like "the login page
  // stopped working, restart everything". Documents are always revalidated;
  // /_next/static assets are content-addressed and stay cacheable.
  async headers() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      {
        source: "/((?!_next/static).*)",
        headers: [
          { key: "Cache-Control", value: "no-store, must-revalidate" },
        ],
      },
    ];
  },
};

module.exports = nextConfig;

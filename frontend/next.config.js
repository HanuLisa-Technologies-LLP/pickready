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
};

module.exports = nextConfig;

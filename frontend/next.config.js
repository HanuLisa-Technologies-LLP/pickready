function contentSecurityPolicy() {
  const directives = {
    "default-src": ["'self'"],
    // checkout.razorpay.com is injected as a <script> by lib/razorpay.ts.
    // The two Google origins are INSURANCE rather than a known requirement, and
    // the asymmetry is the reason. The Firebase Auth v11 popup flow runs through
    // an iframe on our own authDomain (covered by frame-src below), so neither
    // should be needed. But the SDK has historically reached for gapi, the
    // failure mode if it does is that GOOGLE SIGN-IN BREAKS IN PRODUCTION, and
    // that path cannot be exercised from a test. Two named, pinned Google
    // origins cost almost nothing next to that.
    "script-src": [
      "'self'",
      "'unsafe-inline'",
      "'unsafe-eval'",
      "'wasm-unsafe-eval'",
      "https://checkout.razorpay.com",
      "https://apis.google.com",
      "https://www.gstatic.com",
    ],
    // Tailwind and Next both emit inline style attributes.
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:", "https:"],
    "font-src": ["'self'", "data:"],
    // Firebase Auth token exchange, the Razorpay API, and same-origin calls
    // to the backend through the /api proxy. blob: covers the proctoring
    // worker fetching its own vendored model files.
    "connect-src": [
      "'self'",
      "blob:",
      "https://*.googleapis.com",
      "https://*.firebaseapp.com",
      "https://accounts.google.com",
      "https://api.razorpay.com",
      "https://lumberjack.razorpay.com",
    ],
    // The Google sign-in popup and the Razorpay checkout iframe.
    "frame-src": [
      "'self'",
      "https://accounts.google.com",
      "https://*.firebaseapp.com",
      "https://api.razorpay.com",
      "https://checkout.razorpay.com",
    ],
    // Assessment video recording plays back from an object URL.
    "media-src": ["'self'", "blob:", "data:"],
    // The two proctoring inference workers are same origin.
    "worker-src": ["'self'", "blob:"],
    // Clickjacking. This is the modern half; X-Frame-Options below is the
    // half older browsers understand, and both are set on purpose.
    "frame-ancestors": ["'none'"],
    // Nothing in this product embeds a plugin.
    "object-src": ["'none'"],
    // Stops an injected <base> re-pointing every relative URL in the page.
    "base-uri": ["'self'"],
    // A form in this app never posts anywhere but this app.
    "form-action": ["'self'"],
    "upgrade-insecure-requests": [],
  };
  return Object.entries(directives)
    .map(([key, value]) => (value.length ? key + " " + value.join(" ") : key))
    .join("; ");
}

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

  // SAME-ORIGIN FIREBASE AUTH HELPER (/__/auth/*).
  //
  // Sign-in is signInWithPopup everywhere (there is no redirect flow in this
  // codebase), so the MAIN window URL never carries the Firebase handler. What
  // the user sees today is the POPUP's own address bar showing
  // `<project>.firebaseapp.com/__/auth/handler?apiKey=...`. The apiKey there is
  // the public Web API key, a client identifier and not a secret; the only
  // thing worth removing is the vendor domain. This rewrite serves the helper
  // from OUR origin instead: once NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN is set to
  // the site's own host (e.g. readypick.ai), the popup opens
  // readypick.ai/__/auth/handler and this proxies it to Firebase.
  //
  // Two console-side prerequisites before flipping the env var, or Google
  // sign-in breaks: the site host must be in Firebase Auth's authorized
  // domains, and `https://<host>/__/auth/handler` must be an authorized
  // redirect URI on the project's Google OAuth client. Until the flip, this
  // rewrite is inert: nothing links to /__/auth on our origin.
  //
  // Freezing the destination at build time is correct here, unlike the API
  // proxy below: the project id is already baked into the same bundle as
  // NEXT_PUBLIC_FIREBASE_PROJECT_ID, so the rewrite can never disagree with
  // the SDK config it serves.
  async rewrites() {
    const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
    if (!projectId) return [];
    return [
      {
        source: "/__/auth/:path*",
        destination: `https://${projectId}.firebaseapp.com/__/auth/:path*`,
      },
      {
        source: "/__/firebase/:path*",
        destination: `https://${projectId}.firebaseapp.com/__/firebase/:path*`,
      },
    ];
  },

  // NOTE: the same-origin API proxy is deliberately NOT a `rewrites()` entry.
  // Rewrites are resolved during `next build` and frozen into
  // routes-manifest.json, so a destination read from the environment would be
  // captured at build time and pin the image to one backend; an unset variable
  // at build time emits no rewrite at all and API calls fall through to a 404
  // page. It lives in app/api/[...path]/route.ts instead, which reads
  // BACKEND_INTERNAL_URL per request.

  // ---------------------------------------------------------------------
  // SECURITY HEADERS
  //
  // Before this the whole stack shipped exactly ONE security header, HSTS, and
  // nothing else: no framing protection, no MIME-sniffing protection, no
  // referrer policy, no CSP. Notably, this product serves tokenised URLs
  // (/verify-employment/<token>, /portal/outreach/<token>, an assessment
  // invite) and with no Referrer-Policy the full URL, token included, was sent
  // in the Referer header to every third party a page linked out to.
  //
  // ON THE CSP AND `unsafe-inline`. This policy allowlists ORIGINS rather than
  // using a nonce, and that is a deliberate, stated trade rather than an
  // oversight. A nonce CSP in Next has to be minted per request in the proxy
  // middleware, which forces every route into dynamic rendering and gives up
  // static optimisation on the public marketing pages for a benefit this app
  // largely does not collect: there is no `dangerouslySetInnerHTML` anywhere in
  // the tree and React escapes interpolated text, so the reflected-XSS surface
  // `unsafe-inline` protects against is already very small. What the allowlist
  // DOES buy is the part that was missing entirely: a script can no longer be
  // loaded from an arbitrary origin, the page cannot be framed, `base-uri`
  // cannot be hijacked to re-point every relative URL, and `form-action`
  // cannot be pointed at an attacker's collector.
  //
  // `unsafe-eval` is required and is not negotiable here: the proctoring
  // workers run TensorFlow.js, which compiles kernels through `new Function`.
  // `wasm-unsafe-eval` is listed for the same pipeline's WASM backend.
  //
  // Every origin below is one this app genuinely contacts. Fonts are absent on
  // purpose: they are self-hosted through next/font, so `font-src 'self'` is
  // complete and nothing is fetched from Google at runtime.
  async headers() {
    // HSTS. spec-doc6 §13.2 requires it "at the application layer" precisely
    // because an ALB cannot inject a response header, so the load balancer's
    // TLS 1.2 floor and HTTP-to-HTTPS redirect are the whole story without
    // this line. A redirect protects the second request; HSTS protects the
    // first, which is the one an attacker on the network gets to see.
    //
    // Deliberately NOT set in development: it is a browser-persistent
    // commitment keyed on host, and a localhost pin would force HTTPS on every
    // other project that ever serves on the same port.
    //
    // `preload` is omitted on purpose. Submitting to the preload list is a
    // one-way door measured in months to undo, and it commits every current and
    // future subdomain, which is not a decision a config file should make
    // silently. `includeSubDomains` is kept because the ALB terminates TLS for
    // all of them anyway.
    // These four are safe in EVERY environment and carry no host-persistent
    // commitment, unlike HSTS, so they are not gated on NODE_ENV. Shipping them
    // in development too means a violation shows up on a laptop rather than in
    // production.
    const baseline = [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy() },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          // `strict-origin-when-cross-origin` sends the full URL only to the
          // same origin. This is what stops a tokenised verification or
          // outreach URL leaking in the Referer header to any third party the
          // page links out to.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // Camera and microphone are NOT disabled: the proctored assessment
          // needs both, and this product's whole monitoring story depends on
          // them. Everything the product never uses is turned off.
          {
            key: "Permissions-Policy",
            value: [
              "accelerometer=()",
              "autoplay=()",
              "camera=(self)",
              "display-capture=(self)",
              "encrypted-media=()",
              "fullscreen=(self)",
              "geolocation=()",
              "gyroscope=()",
              "magnetometer=()",
              "microphone=(self)",
              "midi=()",
              "payment=(self)",
              "usb=()",
              "xr-spatial-tracking=()",
            ].join(", "),
          },
          { key: "X-DNS-Prefetch-Control", value: "off" },
        ],
      },
    ];

    const security =
      process.env.NODE_ENV === "development"
        ? baseline
        : [
            ...baseline,
            {
              source: "/:path*",
              headers: [
                {
                  key: "Strict-Transport-Security",
                  value: "max-age=63072000; includeSubDomains",
                },
              ],
            },
          ];

    if (process.env.NODE_ENV !== "development") return security;

    // Never let a browser replay an HTML document from a previous dev-server
    // run. Next embeds the build id in every chunk URL, so a cached document
    // served after a restart asks for chunks that no longer exist and the page
    // arrives as dead HTML with no React attached, which looked like "the login
    // page stopped working, restart everything". Documents are always
    // revalidated; /_next/static assets are content-addressed and stay
    // cacheable.
    return [
      ...security,
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

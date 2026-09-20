"use client";

/**
 * The last resort. Next mounts this only when the ROOT LAYOUT itself throws,
 * which means it must supply its own <html> and <body>: the layout that would
 * normally provide them is the thing that just failed.
 *
 * EVERY STYLE HERE IS INLINE, AND THAT IS THE WHOLE POINT. Because the root
 * layout is replaced, so is its `import "./globals.css"`, so is the next/font
 * binding that defines --font-sans, and so is the ThemeProvider. A Tailwind
 * class in this file would resolve to nothing and the user would meet an
 * unstyled stack of black serif text at the single worst moment the product
 * has. So the colours are the navy and teal tokens written out as literals,
 * the type stack degrades to the system font, and nothing here imports
 * anything that could itself fail.
 *
 * For the same reason there is no Button, no Link and no lucide icon: a
 * component that pulls a broken module would take the error page down with it.
 * A plain anchor does a full document load, which is exactly what is wanted
 * when the running app is known to be in a bad state.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#FBFCFE",
          color: "#080F1C",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
          padding: "2rem",
        }}
      >
        <main style={{ maxWidth: "32rem", textAlign: "center" }}>
          <div
            aria-hidden="true"
            style={{
              width: "3rem",
              height: "3rem",
              margin: "0 auto",
              border: "1px solid rgba(180,35,42,0.4)",
              backgroundColor: "rgba(180,35,42,0.08)",
              display: "grid",
              placeItems: "center",
              color: "#B4232A",
              fontSize: "1.5rem",
              lineHeight: 1,
            }}
          >
            !
          </div>

          <h1
            style={{
              marginTop: "1.25rem",
              fontSize: "1.5rem",
              fontWeight: 600,
              letterSpacing: "-0.015em",
            }}
          >
            Vivekium could not start
          </h1>

          <p
            style={{
              marginTop: "0.5rem",
              fontSize: "0.9375rem",
              lineHeight: 1.6,
            }}
          >
            The application failed to load. Nothing you were working on has been
            submitted. Reloading usually resolves it.
          </p>

          <div
            style={{
              marginTop: "1.5rem",
              display: "flex",
              gap: "0.75rem",
              justifyContent: "center",
              flexWrap: "wrap",
            }}
          >
            <button
              type="button"
              onClick={reset}
              style={{
                backgroundColor: "#0A2540",
                color: "#FFFFFF",
                border: "1px solid #0A2540",
                padding: "0.5rem 1rem",
                fontSize: "0.875rem",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Try again
            </button>
            {/* A PLAIN ANCHOR IS DELIBERATE, and next/link is not an option
                here. This file replaces the root layout, so the router that
                `next/link` depends on is part of what just failed; a client
                side navigation would try to re-render the tree that threw. A
                full document load is the only thing that reliably recovers,
                which is exactly what a bare href does. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a
              href="/"
              style={{
                border: "1px solid #0A2540",
                color: "#0A2540",
                padding: "0.5rem 1rem",
                fontSize: "0.875rem",
                fontWeight: 600,
                textDecoration: "none",
              }}
            >
              Reload Vivekium
            </a>
          </div>

          {error.digest ? (
            <p style={{ marginTop: "1.5rem", fontSize: "0.75rem" }}>
              Quote this reference if you contact support
              <span
                style={{
                  display: "block",
                  marginTop: "0.25rem",
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  letterSpacing: "0.05em",
                }}
              >
                {error.digest}
              </span>
            </p>
          ) : null}
        </main>
      </body>
    </html>
  );
}

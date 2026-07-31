"use client";

// Self-healing for a page whose JavaScript chunks no longer exist.
//
// Next embeds a build id in every chunk URL. When the server restarts (a dev
// restart, or a production deploy) the id changes, and any already-open tab, 
// or a browser replaying a cached document, asks for chunks from the previous
// build. Those 404, React never attaches, and the page becomes dead HTML:
// buttons do nothing, the login form doesn't submit. The only way out used to
// be restarting the frontend and backend and hard-refreshing.
//
// A missing chunk is not a state we can recover in place, but it IS a state we
// can detect, and a single hard reload fetches a document referencing the
// CURRENT build. The reload is guarded by a session flag so a genuinely broken
// build can never become an infinite refresh loop.

import * as React from "react";

const RELOAD_FLAG = "pickready:chunk-reload";

function isChunkLoadFailure(message: string): boolean {
  return (
    message.includes("ChunkLoadError") ||
    message.includes("Loading chunk") ||
    message.includes("Loading CSS chunk") ||
    // Webpack/Turbopack surface a stale entry as a bare module-factory error.
    message.includes("Cannot find module") ||
    (message.includes("Failed to fetch dynamically imported module"))
  );
}

export function ChunkRecovery() {
  React.useEffect(() => {
    const recover = (raw: unknown) => {
      const message =
        raw instanceof Error ? `${raw.name}: ${raw.message}` : String(raw ?? "");
      if (!isChunkLoadFailure(message)) return;
      // One attempt per tab. If the reload didn't fix it, the build really is
      // broken and looping would only hide the actual error.
      if (sessionStorage.getItem(RELOAD_FLAG)) return;
      sessionStorage.setItem(RELOAD_FLAG, "1");
      window.location.reload();
    };

    const onError = (event: ErrorEvent) => recover(event.error ?? event.message);
    const onRejection = (event: PromiseRejectionEvent) => recover(event.reason);

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  // Clear the guard once a render has actually succeeded, so a LATER restart in
  // the same tab is still recoverable.
  React.useEffect(() => {
    const timer = setTimeout(() => sessionStorage.removeItem(RELOAD_FLAG), 5000);
    return () => clearTimeout(timer);
  }, []);

  return null;
}

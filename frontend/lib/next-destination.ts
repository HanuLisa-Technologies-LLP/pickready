/**
 * The `?next=` destination, in one place.
 *
 * Three screens had to agree on this and did not: the middleware set `next`,
 * the login flow honoured it, the register flow ignored it entirely, and the
 * portal shell redirected without one. A candidate who clicked an assessment
 * link and chose "Create one" therefore lost the destination at the last step,
 * which is the same bug as never having carried it.
 *
 * The safety rule is the reason this is a function rather than a string read:
 * an emailed link is attacker-influenced input, and `next` decides where a
 * freshly authenticated browser is sent. Only a SAME-ORIGIN PATH is ever
 * honoured, so `//evil.example` and `https://evil.example` are dropped rather
 * than followed.
 */

/** A `next` we are willing to navigate to, or null. */
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw) return null;
  // A single leading slash is a path on this site. Two is a protocol-relative
  // URL, which the browser resolves against ANOTHER host, and a backslash is
  // the same trick some parsers normalise into one.
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
  return raw;
}

/** The `next` currently in the address bar, validated. Null on the server. */
export function currentNextPath(): string | null {
  if (typeof window === "undefined") return null;
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

/**
 * Add `next` to an auth link so switching between Sign in and Create account
 * does not drop the destination.
 */
export function withNext(path: string, next: string | null): string {
  if (!next) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}next=${encodeURIComponent(next)}`;
}

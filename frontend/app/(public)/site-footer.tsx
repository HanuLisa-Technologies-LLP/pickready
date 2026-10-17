/**
 * The public footer: one line, and nothing about who is behind the product
 * (owner, 2026-09-29).
 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-surface/35">
      <div className="mx-auto max-w-6xl px-6 py-8 text-center text-sm text-muted-foreground lg:px-10">
        &copy; {new Date().getFullYear()} Vivekium
      </div>
    </footer>
  );
}

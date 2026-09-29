/**
 * The public footer: one line, and nothing about who is behind the product
 * (owner, 2026-09-29).
 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto max-w-6xl px-6 py-4 text-center text-sm lg:px-10">
        &copy; {new Date().getFullYear()} ReadyPick
      </div>
    </footer>
  );
}

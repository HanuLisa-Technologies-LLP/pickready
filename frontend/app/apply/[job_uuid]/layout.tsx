import type { Metadata } from "next";

/**
 * The apply page itself is a Client Component (it holds the whole application
 * form in React state), so it cannot export `metadata`. This layout supplies
 * the tab title instead.
 *
 * The title is the page name only: `app/layout.tsx` appends "| ReadyPick"
 * through a template, so repeating the product name here would render it
 * twice. The role's own title is not used, because it is not known until the
 * public job fetch resolves on the client.
 */
export const metadata: Metadata = { title: "Apply" };

export default function ApplyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

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
 *
 * INDEXABLE, AND STATED RATHER THAN INHERITED. Every other unauthenticated
 * route in this product is token-addressed and carries noindex, so the absence
 * of a robots directive here would read as an oversight rather than a
 * decision. This page is the opposite case: it renders a full job description
 * to an unauthenticated visitor, it is the destination every employer page
 * links to, and it carries JobPosting structured data. It is meant to be found.
 */
export const metadata: Metadata = {
  title: "Apply",
  robots: { index: true, follow: true },
};

export default function ApplyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

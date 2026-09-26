import type { Metadata } from "next";

/**
 * NOINDEX for the whole Customer Portal.
 *
 * The shell one level down (`app/(org)/org/layout.tsx`) is a Client Component,
 * so it cannot export `metadata` at all. This layout exists only to carry that
 * export: it is a Server Component that renders its children unchanged, so it
 * adds no markup, no wrapper and no behaviour, and the nested client shell is
 * untouched.
 *
 * Jobs, candidates, reports and billing are one customer's private workspace.
 * Nothing under here is content for a search engine, and a crawled URL from
 * this tree is a signed-out redirect at best.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function OrgGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

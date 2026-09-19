import type { Metadata } from "next";

/**
 * NOINDEX for the whole Business Development Portal.
 *
 * The shell one level down (`app/(bd)/bd/layout.tsx`) is a Client Component,
 * so it cannot export `metadata` at all. This layout exists only to carry that
 * export: it is a Server Component that renders its children unchanged, so it
 * adds no markup, no wrapper and no behaviour, and the nested client shell is
 * untouched.
 *
 * Leads, AI Reach results and prospect companies are ReadyPick's own sales
 * working notes about other businesses.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function BdGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

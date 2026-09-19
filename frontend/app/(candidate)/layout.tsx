import type { Metadata } from "next";

/**
 * NOINDEX for the whole Candidate Portal.
 *
 * The shell one level down (`app/(candidate)/portal/(app)/layout.tsx`) is a
 * Client Component, so it cannot export `metadata` at all. This layout exists
 * only to carry that export: it is a Server Component that renders its
 * children unchanged, so it adds no markup, no wrapper and no behaviour, and
 * the nested client shell is untouched.
 *
 * It covers the signed-in portal and the token-addressed outreach form that
 * sits beside it. A candidate's applications, updates and messages are theirs.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function CandidateGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

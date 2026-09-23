import type { Metadata } from "next";

/**
 * NOINDEX. The page itself is a Client Component and cannot export metadata.
 *
 * This URL carries a verification token in its path and is sent to one named
 * person at a previous employer. Indexing it would publish both the token and
 * the fact that a particular company was asked about a particular candidate.
 */
export const metadata: Metadata = {
  title: "Employment verification",
  robots: { index: false, follow: false },
};

export default function VerifyEmploymentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

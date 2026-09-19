import type { Metadata } from "next";

/**
 * NOINDEX. The page itself is a Client Component and cannot export metadata.
 *
 * This URL carries an outreach token in its path. It is sent to one sourced
 * candidate so they can complete their profile without an account, so the URL
 * alone is the credential.
 *
 * The (candidate) group layout already sets noindex over this whole tree. This
 * restates it at the route that most needs it, because a group-level rule is
 * the kind that gets relaxed later for a reason that has nothing to do with a
 * tokenised link.
 */
export const metadata: Metadata = {
  title: "Complete your profile",
  robots: { index: false, follow: false },
};

export default function OutreachLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

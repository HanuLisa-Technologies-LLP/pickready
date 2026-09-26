import type { Metadata } from "next";

/**
 * NOINDEX. The page itself is a Client Component and cannot export metadata.
 *
 * This URL carries an assessment invitation token in its path. A crawled token
 * is a leaked token: anyone holding it can open the assessment as the invited
 * candidate. robots.txt disallows /assessments as well; this is the half that
 * still binds when a crawler arrives at the URL by another route.
 */
export const metadata: Metadata = {
  title: "Assessment invitation",
  robots: { index: false, follow: false },
};

export default function AssessmentInviteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

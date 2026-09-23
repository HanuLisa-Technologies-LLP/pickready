import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginFlow } from "@/components/login-flow";

// A sign-in form is not content. `index: false` keeps it out of results;
// `follow` is left alone so the links out of the page still carry weight.
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false },
};

// ONE login for everyone (contract rev 2): Owner, all client-org roles and
// candidates. Firebase sign-in (Google / email+password / phone); routing after
// exchange is portal-driven. Suspense guards any client-only hooks in the flow.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginFlow title="Sign in to Vivekium" />
    </Suspense>
  );
}

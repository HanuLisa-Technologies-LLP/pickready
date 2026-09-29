import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginPageFlow } from "@/components/login-flow";

// A sign-in form is not content. `index: false` keeps it out of results;
// `follow` is left alone so the links out of the page still carry weight.
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false },
};

// The CANDIDATE sign-in page (Google or email and password, with a security
// check). Company team members sign in at /company/login; the Provider and
// business development sign-ins are `?portal=owner` and `?portal=bd` here.
// Suspense guards the search-param read in the flow.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginPageFlow />
    </Suspense>
  );
}

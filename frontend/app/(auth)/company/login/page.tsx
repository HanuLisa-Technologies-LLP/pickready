import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginFlow } from "@/components/login-flow";

export const metadata: Metadata = {
  title: "Company sign in",
  robots: { index: false },
};

// Every company role (the Company Super Admin, managers, recruiters, hiring
// and interview managers, and the leadership roles) signs in HERE, with an
// email, a password and a security check. There is no Google button: the
// server refuses a Google sign-in for a company account even when a caller
// skips this page (auth spec 2.6 and 7.2). Team members arrive through their
// invitation first; nobody creates a company account from this page.
export default function CompanyLoginPage() {
  return (
    <Suspense>
      <LoginFlow
        surface="company"
        title="Sign in to your company workspace"
        description="Use the email address your company invited you with."
      />
    </Suspense>
  );
}

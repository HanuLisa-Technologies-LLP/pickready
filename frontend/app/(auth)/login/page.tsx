import { Suspense } from "react";

import { LoginFlow } from "@/components/login-flow";

export const metadata = { title: "Sign in" };

// ONE login for everyone (contract rev 2): Owner, all client-org roles and
// candidates. Firebase sign-in (Google / email+password / phone); routing after
// exchange is portal-driven. Suspense guards any client-only hooks in the flow.
export default function LoginPage() {
  return (
    <Suspense>
      <LoginFlow title="Sign in to PickReady" />
    </Suspense>
  );
}

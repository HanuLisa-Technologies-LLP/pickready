import { Suspense } from "react";

import { LoginFlow } from "@/components/login-flow";

export const metadata = { title: "Sign in — PickReady" };

// ONE login for everyone (contract rev 2): Owner, all client-org roles and
// candidates. Routing after verify is portal-driven. Suspense wraps the flow
// because it reads useSearchParams (?identifier= prefill from sign-up).
export default function LoginPage() {
  return (
    <Suspense>
      <LoginFlow
        title="Sign in to PickReady"
        description="One login for everyone — owners, client teams and candidates. OTP only, no passwords."
      />
    </Suspense>
  );
}

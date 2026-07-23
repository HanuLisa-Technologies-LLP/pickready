import { LoginFlow } from "@/components/login-flow";

export const metadata = { title: "Sign in — PickReady" };

// ONE login for everyone (contract rev 2): Owner, all client-org roles and
// candidates. Routing after verify is portal-driven.
export default function LoginPage() {
  return (
    <LoginFlow
      title="Sign in to PickReady"
      description="One login for everyone — owners, client teams and candidates. OTP only, no passwords."
    />
  );
}

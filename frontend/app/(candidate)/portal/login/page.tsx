import { LoginFlow } from "@/components/login-flow";

export const metadata = { title: "Candidate sign in — PickReady" };

export default function PortalLoginPage() {
  return (
    <LoginFlow
      audience="candidate"
      title="Candidate Portal"
      description="Sign in with a one-time code to see new jobs, apply, and track your applications."
    />
  );
}

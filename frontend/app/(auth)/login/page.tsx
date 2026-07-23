import { LoginFlow } from "@/components/login-flow";

export const metadata = { title: "Sign in — PickReady" };

export default function LoginPage() {
  return (
    <LoginFlow
      audience="internal"
      title="Sign in to PickReady"
      description="For Hanulisa staff, clients and hiring managers. OTP only — no passwords."
    />
  );
}

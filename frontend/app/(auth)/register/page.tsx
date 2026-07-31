import { RegisterFlow } from "@/components/register-flow";

export const metadata = { title: "Create account" };

// Candidate self sign-up (register first, log in later). Public route.
export default function RegisterPage() {
  return <RegisterFlow />;
}

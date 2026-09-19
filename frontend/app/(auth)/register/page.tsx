import type { Metadata } from "next";

import { RegisterFlow } from "@/components/register-flow";

// A sign-up form is not content. `index: false` keeps it out of results;
// `follow` is left alone so the links out of the page still carry weight.
export const metadata: Metadata = {
  title: "Create account",
  robots: { index: false },
};

// Candidate self sign-up (register first, log in later). Public route.
export default function RegisterPage() {
  return <RegisterFlow />;
}

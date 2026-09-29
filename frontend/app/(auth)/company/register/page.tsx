import type { Metadata } from "next";

import { CompanyRegisterFlow } from "@/components/company-register-flow";

export const metadata: Metadata = {
  title: "Register your company",
  robots: { index: false },
};

// The first Company Super Admin registers the company here (owner spec
// 2026-09-29, section 2.2): details and a security check, a security code from
// the company mailbox, the paid Starter pilot, then a password. Every other
// company role joins through an invitation instead.
export default function CompanyRegisterPage() {
  return <CompanyRegisterFlow />;
}

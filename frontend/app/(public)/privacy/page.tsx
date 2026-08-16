import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy",
  description: "How ReadyPick handles candidate, customer and visitor information.",
};

const SECTIONS = [
  ["What we collect", "Account identifiers, profile and resume information, role applications, assessment responses, validation records, communications, customer workspace activity, billing records and security/audit events. We collect only what the relevant workflow requires."],
  ["Why we process it", "To provide the service, match profiles to roles, administer assessments, prepare PPI Assessment Reports, support customer decisions, send requested workflow messages, prevent misuse, meet legal obligations and improve reliability. We do not use a protected characteristic to make a candidate decision."],
  ["Consent and notices", "Where processing relies on consent, the interface states the purpose before collection. Separate controls cover background or previous-employer verification and retention in the ReadyPick Databank for future role matching. Withdrawing Databank consent stops future reuse; it does not erase records that must be retained for a live application or legal obligation."],
  ["AI and human decisions", "AI assists with extraction, matching, drafting and structured analysis. It does not make the final customer decision. Customers remain responsible for reviewing evidence and deciding whether to progress a candidate. ReadyPick records key actions for later review."],
  ["Sharing", "Candidate information is shared with the customer workspace connected to the role and with service providers that help us operate hosting, authentication, storage, communication, payment and AI services under appropriate contractual and security controls. We do not sell personal data."],
  ["Retention and deletion", "We retain information only for the stated purpose, a customer’s documented retention setting, a legal requirement or the period needed to resolve a dispute. When no lawful purpose remains, information is deleted or irreversibly de-identified. Where applicable, advance notice is provided before account-linked data is erased for inactivity."],
  ["Security and incidents", "Controls include tenant isolation, role and capability checks, encryption in transit, protected session cookies, append-only audit records and restricted provider credentials. If a personal-data breach occurs, ReadyPick follows the notification and remediation duties that apply at that time."],
  ["Your choices and rights", "Subject to applicable law, you may request access to a summary of your personal data and processing, correction or completion, erasure, consent withdrawal, grievance handling and nomination. We may need to verify identity before acting on a request."],
] as const;

export default function PrivacyPage() {
  return (
    <main id="main" className="mx-auto max-w-4xl px-6 py-20 lg:px-10 lg:py-28">
      <p className="text-sm font-semibold uppercase tracking-[.18em] text-brand-600">Privacy notice</p>
      <h1 className="mt-4 text-4xl font-bold">Your information should never be a mystery</h1>
      <p className="mt-5 text-lg leading-8">
        This notice explains how ReadyPick handles digital personal data. It is designed to support the Digital Personal Data Protection Act, 2023 and the Digital Personal Data Protection Rules, 2025 as their provisions become applicable.
      </p>
      <p className="mt-3 text-sm">Effective: 29 July 2026</p>
      <div className="mt-12 space-y-10">
        {SECTIONS.map(([title, body]) => (
          <section key={title}>
            <h2 className="text-xl font-semibold">{title}</h2>
            <p className="mt-3 text-pretty leading-8">{body}</p>
          </section>
        ))}
        <section>
          <h2 className="text-xl font-semibold">Contact and grievance handling</h2>
          <p className="mt-3 leading-8">
            Write to{" "}
            <a className="font-semibold text-brand-600 underline underline-offset-4" href="mailto:manjuchro@gmail.com">
              manjuchro@gmail.com
            </a>{" "}
            with the subject “Privacy request”. We aim to acknowledge a grievance within 7 days and provide a substantive response within 30 days. If you are not satisfied, you may use the remedies available under applicable law.
          </p>
        </section>
      </div>
      <p className="mt-12 border-t border-border pt-6 text-sm leading-6">
        Also read our <Link className="font-semibold text-brand-600 underline" href="/terms">Terms of Use</Link>.
      </p>
    </main>
  );
}

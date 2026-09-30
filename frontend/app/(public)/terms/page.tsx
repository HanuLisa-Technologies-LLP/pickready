import type { Metadata } from "next";
import { publicPageMetadata } from "@/lib/site";

export const metadata: Metadata = publicPageMetadata({
  path: "/terms",
  title: "Terms",
  description:
    "Terms governing access to and use of ReadyPick.",
});

const TERMS = [
  ["Using ReadyPick", "You may use ReadyPick only through an authorised account and for lawful candidate and people operations. Keep account credentials secure, provide accurate information and promptly remove access for people who no longer need it."],
  ["Customer responsibility", "Customers decide which roles to publish, which candidates to progress and what messages to send. AI output is decision support and must be reviewed by an authorised team member. Customers are responsible for having a lawful basis and giving required notices for data they upload or ask ReadyPick to process."],
  ["Candidate responsibility", "Candidates must submit information they reasonably believe is accurate and must not impersonate another person. Assessment and validation responses should be their own unless the workflow expressly permits assistance."],
  ["Plans and payment", "ReadyPick plans renew monthly in Indian rupees at the displayed plan price plus GST. There is no setup fee, annual commitment or lock-in. The customer may cancel at any time; cancellation stops future renewals at the end of the paid period. Each paid month adds the displayed number of completed assessments to a pool shared across open jobs. Unused monthly credits remain valid for three months from grant, including after cancellation. The only one-time top-up is the Starter pack."],
  ["Acceptable use", "Do not attempt to bypass access controls, scrape private workspaces, reverse engineer security measures, upload malware, interfere with service availability, use the service for unlawful discrimination or submit information you are not entitled to share."],
  ["Intellectual property", "ReadyPick, the Tatva Assessment, the PRISM Report, product design, software and platform content belong to ReadyPick or its licensors. Customers and candidates retain rights in the information they submit and grant the limited permission needed to operate the requested service."],
  ["Availability and changes", "We work to provide a low-latency, reliable service but cannot promise uninterrupted availability. We may change features to improve safety, reliability or legal compliance. Material changes affecting purchased credits or personal-data handling will be communicated through reasonable channels."],
  ["Suspension and termination", "We may suspend access to protect users, investigate misuse, comply with law or address unpaid charges. Data export and deletion on termination follow the applicable agreement, privacy notice and legal retention duties."],
  ["Liability", "To the extent permitted by law, ReadyPick is not responsible for a customer’s final candidate decision, indirect losses or information supplied by another user. Nothing in these terms limits a right or liability that cannot lawfully be limited."],
  ["Governing law", "These terms are governed by the laws of India. Courts with jurisdiction in Hyderabad, Telangana will have jurisdiction, subject to any mandatory dispute-resolution rights that apply."],
] as const;

export default function TermsPage() {
  return (
    <main id="main" className="mx-auto max-w-4xl px-6 py-20 lg:px-10 lg:py-28">
      <p className="type-eyebrow text-brand-600">Terms of use</p>
      <h1 className="mt-4 type-section-title">Clear responsibilities make the platform work</h1>
      <p className="mt-5 type-lead">
        These terms govern access to ReadyPick. A signed customer agreement or order form may add commercial terms; if it conflicts with this page, the signed document controls for that customer.
      </p>
      <p className="mt-3 text-sm">Effective: 30 September 2026</p>
      <div className="mt-12 space-y-10">
        {TERMS.map(([title, body], index) => (
          <section key={title}>
            <h2 className="text-xl font-semibold">{index + 1}. {title}</h2>
            <p className="mt-3 text-pretty leading-8">{body}</p>
          </section>
        ))}
        <section>
          <h2 className="text-xl font-semibold">11. Contact</h2>
          <p className="mt-3 leading-8">
            Questions about these terms may be sent to{" "}
            <a className="font-semibold text-brand-600 underline underline-offset-4" href="mailto:manjuchro@gmail.com">
              manjuchro@gmail.com
            </a>.
          </p>
        </section>
      </div>
    </main>
  );
}

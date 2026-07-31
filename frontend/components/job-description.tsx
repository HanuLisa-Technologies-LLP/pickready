"use client";

// Shared, candidate-facing JD renderer.
//
// Live JD payloads mix shapes: `responsibilities`/`accountabilities` come back
// as string[] from the AI generator but as a paragraph when typed by hand, and
// `reportees`/`experience_years` can be numbers. Everything is normalised here
// rather than assuming one shape and rendering "[object Object]".
//
// Field-name defence: the backend emits the JD under `jd_json` on some reads
// and `jd` on others. `pickJd()` accepts either so a candidate never sees an
// empty description when the data is actually present.

import { Badge } from "@/components/ui/badge";

export function asJdLines(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((v) => String(v).trim()).filter(Boolean);
  }
  if (typeof value === "number") return [String(value)];
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return [];
    const lines = trimmed
      .split(/\r?\n+/)
      .map((l) => l.replace(/^[-•*]\s*/, "").trim())
      .filter(Boolean);
    return lines.length > 1 ? lines : [trimmed];
  }
  return [];
}

/** The JD object under whichever key the backend used. Never undefined. */
export function pickJd(source: {
  jd_json?: Record<string, unknown> | null;
  jd?: Record<string, unknown> | null;
} | null | undefined): Record<string, unknown> {
  return (source?.jd_json ?? source?.jd ?? {}) as Record<string, unknown>;
}

export function hasJdContent(jd: Record<string, unknown>): boolean {
  return Object.values(jd).some((v) => asJdLines(v).length > 0);
}

export function JdBlock({ title, value }: { title: string; value: unknown }) {
  const lines = asJdLines(value);
  if (lines.length === 0) return null;
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide">{title}</h3>
      {lines.length === 1 ? (
        <p className="whitespace-pre-line text-sm leading-relaxed">{lines[0]}</p>
      ) : (
        <ul className="list-[circle] space-y-1 pl-5 text-sm leading-relaxed">
          {lines.map((line, i) => (
            <li key={`${title}-${i}`}>{line}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The employer fields a candidate-facing job payload carries. */
export interface CompanyContext {
  company_name?: string | null;
  company_about?: string | null;
  company_culture?: string | null;
  company_industry?: string | null;
  company_benefits?: string | null;
}

export function hasCompanyContent(job: CompanyContext | null | undefined): boolean {
  return Boolean(
    job?.company_about ||
      job?.company_culture ||
      job?.company_industry ||
      job?.company_benefits
  );
}

/**
 * About the employer and how they work, shown alongside every JD a candidate
 * reads. Choosing a role is choosing a company, so this is not optional detail
 * tucked away behind another click (client decision, 2026-07-27).
 */
export function CompanySummary({ job }: { job: CompanyContext | null | undefined }) {
  if (!hasCompanyContent(job)) return null;
  return (
    <div className="space-y-4">
      <JdBlock
        title={job?.company_name ? `About ${job.company_name}` : "About the company"}
        value={job?.company_about}
      />
      <JdBlock title="Industry" value={job?.company_industry} />
      <JdBlock title="Company culture" value={job?.company_culture} />
      <JdBlock title="Benefits" value={job?.company_benefits} />
    </div>
  );
}

/**
 * Role, responsibilities, skills, education and experience, everything the
 * candidate needs to know what they are applying to. Renders an honest empty
 * state instead of a blank panel when the employer published a sparse JD.
 */
export function JobDescriptionSummary({
  jd,
  loading,
  className,
}: {
  jd: Record<string, unknown>;
  loading?: boolean;
  className?: string;
}) {
  if (loading) {
    return (
      <p role="status" className="text-sm">
        Loading the job description
      </p>
    );
  }
  if (!hasJdContent(jd)) {
    return (
      <p className="text-sm">
        The employer hasn&apos;t published a detailed description for this role
        yet. Reach out to them if you need more context before applying.
      </p>
    );
  }
  const skills = asJdLines(jd.skills);
  const experience =
    typeof jd.experience_years === "number" && jd.experience_years > 0
      ? `${jd.experience_years}+ years`
      : asJdLines(jd.experience_years)[0] ?? null;
  return (
    <div className={className}>
      <div className="space-y-4">
        <JdBlock title="About this role" value={jd.description} />
        <JdBlock title="Role" value={jd.role} />
        <JdBlock title="Responsibilities" value={jd.responsibilities} />
        <JdBlock title="Accountabilities" value={jd.accountabilities} />
        <JdBlock title="Education" value={jd.education} />
        {experience ? <JdBlock title="Experience" value={experience} /> : null}
        {skills.length > 0 ? (
          <section className="space-y-1.5">
            <h3 className="text-xs font-semibold uppercase tracking-wide">
              Skills
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {skills.map((s) => (
                <Badge key={s} variant="secondary">
                  {s}
                </Badge>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}

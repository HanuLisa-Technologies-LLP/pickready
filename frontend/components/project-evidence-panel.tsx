"use client";

// Recruiter-facing Project Evidence (Project Evidence Intelligence,
// 2026-09-01). Renders DERIVED intelligence only: the candidate's claim and
// the system's observed evidence are labelled separately, strength is a word
// (never a number), and there is no original-file download because the
// product does not retain originals. A candidate with no projects is a
// normal, unpunished state and reads as one.

import * as React from "react";
import { FolderGit2 } from "lucide-react";

import { apiGet } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

type ClaimAssessment = {
  claim: string;
  supporting_evidence: string[];
  limiting_evidence: string[];
  assessment: string;
};

type ProjectEvidence = {
  id: string;
  name: string;
  status: string;
  submission_kind: string;
  repository_url: string | null;
  domains: string[];
  candidate_description: string;
  technologies: string[];
  observed_evidence: string[];
  documentation_evidence: string[];
  claim_assessments: ClaimAssessment[];
  synthesis: string | null;
  evidence_strength: string | null;
  potential_gaps: string[];
  validation_areas: string[];
  uncertainties: string[];
  processed_at: string | null;
};

export function ProjectEvidencePanel({ candidateId }: { candidateId: string }) {
  const [projects, setProjects] = React.useState<ProjectEvidence[] | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setProjects(null);
    apiGet<{ projects: ProjectEvidence[] }>(
      `/candidates/${candidateId}/project-evidence`
    )
      .then((res) => {
        if (!cancelled) setProjects(res.projects);
      })
      .catch(() => {
        if (!cancelled) setProjects(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  if (loading) {
    return (
      <p role="status" className="text-sm">
        Loading project evidence
      </p>
    );
  }
  if (!projects || projects.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-5">
        <p className="font-medium">No project evidence submitted</p>
        <p className="mt-1 text-sm">
          This candidate has not added projects to their profile. Their
          resume, validation answers and assessment remain the evidence base.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {projects.map((project) => (
        <div key={project.id} className="rounded-md border border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <FolderGit2 className="h-4 w-4 shrink-0" aria-hidden="true" />
              <h4 className="truncate text-sm font-semibold">{project.name}</h4>
            </div>
            {project.evidence_strength ? (
              <Badge
                variant="outline"
                className="border-teal-600/40 text-teal-700 dark:text-teal-300"
              >
                {project.evidence_strength} evidence
              </Badge>
            ) : (
              <Badge variant="secondary">Evidence summary only</Badge>
            )}
          </div>

          {project.domains.length > 0 ? (
            <p className="mt-1 text-xs">{project.domains.join(" / ")}</p>
          ) : null}

          <div className="mt-3 space-y-3 text-sm">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide">
                Candidate&apos;s description (their claim)
              </p>
              <p className="mt-1">{project.candidate_description}</p>
            </div>

            {project.technologies.length > 0 ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  Observed stack
                </p>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {project.technologies.map((tech) => (
                    <Badge key={tech} variant="secondary">
                      {tech}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}

            {project.observed_evidence.length > 0 ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  System-observed evidence
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {project.observed_evidence.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {project.synthesis ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  Assessment
                </p>
                <p className="mt-1">{project.synthesis}</p>
              </div>
            ) : null}

            {project.claim_assessments.length > 0 ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  Claims against evidence
                </p>
                <div className="mt-1 space-y-2">
                  {project.claim_assessments.map((entry) => (
                    <div
                      key={entry.claim}
                      className="rounded-md border border-border p-2"
                    >
                      <p className="font-medium">{entry.claim}</p>
                      <p className="mt-0.5 text-xs">
                        Assessment: {entry.assessment}
                      </p>
                      {entry.supporting_evidence.length > 0 ? (
                        <p className="mt-0.5 text-xs">
                          Supported by: {entry.supporting_evidence.join("; ")}
                        </p>
                      ) : null}
                      {entry.limiting_evidence.length > 0 ? (
                        <p className="mt-0.5 text-xs">
                          Limited by: {entry.limiting_evidence.join("; ")}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {project.potential_gaps.length > 0 ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  Evidence gaps
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {project.potential_gaps.map((gap) => (
                    <li key={gap}>{gap}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {project.validation_areas.length > 0 ? (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide">
                  Worth validating in interview
                </p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {project.validation_areas.map((area) => (
                    <li key={area}>{area}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {project.uncertainties.length > 0 ? (
              <>
                <Separator />
                <p className="text-xs">
                  Processing notes: {project.uncertainties.join(" ")}
                </p>
              </>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

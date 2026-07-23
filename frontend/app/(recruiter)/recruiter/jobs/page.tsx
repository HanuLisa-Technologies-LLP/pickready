"use client";

import { JobsList } from "@/components/jobs-list";

export default function RecruiterJobsIndexPage() {
  return (
    <JobsList
      basePath="/recruiter/jobs"
      description="Ratified jobs assigned to you. Open a job to upload resumes, view matches and schedule interviews."
    />
  );
}

"use client";

import { JobsList } from "@/components/jobs-list";

export default function HrJobsPage() {
  return (
    <JobsList
      basePath="/hr/jobs"
      description="Ratified jobs assigned to you. Open a job to manage compensation, matching and outreach."
    />
  );
}

// My Profile, the candidate's single, unified profile (client decision,
// 2026-07-27). It replaces the old "Settings" page and now holds everything a
// candidate maintains about themselves in one place:
//
//   * account details (name, phone) and, for password accounts, the password;
//   * the MAIN resume, uploadable and re-uploadable at any time;
//   * the advanced form, the 40 validation answers, given once here instead of
//     being re-asked inside every job's assessment;
//   * the appearance toggle (the only place it lives, claude.md rule 10).
//
// Role is deliberately not shown: a candidate has exactly one.

import { SettingsPage } from "@/components/settings-page";
import { CandidateProfileForm } from "@/components/candidate-profile-form";
import { MainResumeCard } from "@/components/main-resume-card";
import { ProjectsSection } from "@/components/projects-section";

export const metadata = { title: "My Profile" };

export default function CandidateProfilePage() {
  return (
    <SettingsPage
      title="My Profile"
      description="Your details, your main resume, and the answers reused on every application."
      showRole={false}
    >
      <MainResumeCard />
      <ProjectsSection />
      <CandidateProfileForm />
    </SettingsPage>
  );
}

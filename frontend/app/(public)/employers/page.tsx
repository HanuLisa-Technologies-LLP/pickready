import type { Metadata } from "next";

import { EmployerDirectory } from "./employer-directory";

// The public employer directory (2026-09-05 add-features spec, "Employer Page
// & Content"): anyone can search client companies and open their pages. The
// layout template appends "| ReadyPick", so the title is just the page's name.
export const metadata: Metadata = {
  title: "Employers",
  description:
    "Search companies hiring through ReadyPick and browse their open roles.",
};

export default function EmployersPage() {
  return (
    <main id="main">
      <EmployerDirectory />
    </main>
  );
}

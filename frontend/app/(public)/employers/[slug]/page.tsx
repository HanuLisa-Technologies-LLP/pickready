import type { Metadata } from "next";

import { EmployerProfile } from "./employer-profile";

// One employer's public page (2026-09-05 add-features spec, "Employer Page &
// Content"). The tab title is derived from the slug, which IS the company
// name in URL form, so a fetch is not needed to name the tab; the layout
// template appends "| ReadyPick".
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const name = slug
    .split("-")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
  return {
    title: name || "Employer",
    description: `Company profile and open roles at ${name || "this employer"}.`,
  };
}

export default async function EmployerSlugPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <main id="main">
      <EmployerProfile slug={slug} />
    </main>
  );
}

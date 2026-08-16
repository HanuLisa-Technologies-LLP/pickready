import { ReachPage } from "@/components/bd/reach-page";

// `app/layout.tsx` appends "| ReadyPick" through a template, so the title here
// is just the page name.
export const metadata = { title: "BD Reach" };

// BD Reach is the BD Portal index: it is where a rep starts their day.
//
// It was two pages, Personal Reach and Social Reach, until 2026-08-09. They
// were one funnel over one table the whole time, so the split forced a rep to
// decide which screen a company belonged on before they could work it, and made
// "how many leads are we working" a question with two answers. Where a lead came
// from is now a column and a filter on this one page.
export default function BDReachPage() {
  return (
    <ReachPage
      title="BD Reach"
      description="Every company the team is working, however it was found, and how far each one has got."
    />
  );
}

import type { Metadata } from "next";

import { JoinFlow } from "@/components/join-flow";

// An invitation acceptance page is addressed by a token in the query string
// and is not content. `index: false` keeps it out of results; `follow` is left
// alone so the links out of the page still carry weight.
export const metadata: Metadata = {
  title: "Join your team",
  robots: { index: false },
};

export default function JoinPage({
  searchParams,
}: {
  searchParams: { invite?: string; invite_code?: string };
}) {
  return (
    <JoinFlow token={searchParams.invite ?? searchParams.invite_code ?? ""} />
  );
}

import { JoinFlow } from "@/components/join-flow";

export const metadata = { title: "Join your team" };

export default function JoinPage({
  searchParams,
}: {
  searchParams: { invite?: string; invite_code?: string };
}) {
  return (
    <JoinFlow token={searchParams.invite ?? searchParams.invite_code ?? ""} />
  );
}

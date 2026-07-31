export type OutreachMode = "ai" | "manual";

export type OutreachRecipient = {
  link_id: string;
};

export function buildOutreachComposePayload(
  jobId: string,
  recipients: OutreachRecipient[],
  mode: OutreachMode,
  manual?: { subject: string; body: string },
) {
  const linkIds = recipients.map((recipient) => recipient.link_id).filter(Boolean);
  if (!linkIds.length) {
    throw new Error("Select at least one candidate before composing an email.");
  }
  return {
    job_id: jobId,
    link_ids: linkIds,
    mode,
    ...(mode === "manual"
      ? { subject: manual?.subject ?? "", body: manual?.body ?? "" }
      : {}),
  };
}

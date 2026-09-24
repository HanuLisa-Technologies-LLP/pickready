// @vitest-environment jsdom
//
// The composer's sender (vivekium release, Phase 6, audit P1-3.6): nothing
// on this screen ever sent `sender_id`, so an approved corporate mailbox was
// never used. What the modal owns is WHICH sender it posts; that the server
// stores it and re-validates it at send time is tested on the server.

import * as React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EmailSender, RankedCandidate } from "@/lib/types";

const api = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("@/lib/api", () => api);
const toast = vi.fn();
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
const permissions = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({
    can: (capability: string) => permissions.granted.has(capability),
    loading: false,
    capabilities: [...permissions.granted],
  }),
}));

import { EmailCompositionModal } from "./email-composition-modal";

const CANDIDATES = [
  { link_id: "link-1", full_name: "Asha Rao" },
] as unknown as RankedCandidate[];

function sender(overrides: Partial<EmailSender>): EmailSender {
  return {
    id: "s-1",
    name: "Hiring desk",
    email: "hr@acme.example",
    status: "active",
    email_verified: false,
    authorized_at: "2026-09-20T10:00:00Z",
    created_at: "2026-09-19T10:00:00Z",
    can_send: true,
    sending_detail: "This address can send.",
    is_default: false,
    ...overrides,
  };
}

function renderModal() {
  return render(
    <EmailCompositionModal
      open
      onOpenChange={() => undefined}
      candidates={CANDIDATES}
      jobTitle="Data Engineer"
      companyName="Acme"
    />
  );
}

/** Write the email by hand, so the send needs no drafting round trip. */
async function sendManually() {
  fireEvent.click(screen.getByRole("button", { name: /write it myself/i }));
  fireEvent.change(screen.getByLabelText(/Subject/), {
    target: { value: "Next steps" },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^Send 1$/ }));
  });
  await waitFor(() =>
    expect(api.apiPost).toHaveBeenCalledWith("/emails/send", expect.anything())
  );
  return api.apiPost.mock.calls.find((call) => call[0] === "/emails/send")![1];
}

beforeEach(() => {
  api.apiGet.mockReset();
  api.apiPost.mockReset();
  api.apiPost.mockResolvedValue({ queued: 1, skipped: [] });
  toast.mockReset();
  permissions.granted = new Set();
});
afterEach(() => cleanup());

describe("EmailCompositionModal sender", () => {
  it("sends under the tenant's default sender, preselected", async () => {
    permissions.granted = new Set(["manage_email_senders"]);
    api.apiGet.mockResolvedValue({
      senders: [
        sender({ id: "s-2", email: "talent@acme.example" }),
        sender({ id: "s-1", is_default: true }),
        sender({ id: "s-3", status: "revoked", email: "old@acme.example" }),
      ],
      can_manage: true,
      can_authorize: false,
    });
    renderModal();

    await screen.findByText("Send from");
    expect(api.apiGet).toHaveBeenCalledWith("/email-senders");
    const body = await sendManually();
    expect(body.sender_id).toBe("s-1");
  });

  it("posts no sender when the tenant has no default", async () => {
    permissions.granted = new Set(["manage_email_senders"]);
    api.apiGet.mockResolvedValue({
      senders: [sender({ id: "s-2" })],
      can_manage: true,
      can_authorize: false,
    });
    renderModal();

    await screen.findByText("Send from");
    const body = await sendManually();
    expect("sender_id" in body).toBe(false);
  });

  it("never asks for the senders list without the capability to read it", async () => {
    renderModal();

    expect(
      screen.getByText(/default sender, or the Vivekium mailbox/)
    ).toBeTruthy();
    const body = await sendManually();
    expect(api.apiGet).not.toHaveBeenCalled();
    expect("sender_id" in body).toBe(false);
  });

  it("says so when the senders cannot be loaded, and still sends", async () => {
    permissions.granted = new Set(["manage_email_senders"]);
    api.apiGet.mockRejectedValue(new Error("Service unavailable"));
    renderModal();

    expect(
      await screen.findByText(/Your senders could not be loaded/)
    ).toBeTruthy();
    const body = await sendManually();
    expect("sender_id" in body).toBe(false);
  });
});

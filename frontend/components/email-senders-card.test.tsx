// @vitest-environment jsdom
//
// The default sender controls (vivekium release, Phase 6, audit P1-3.6).
//
// What the card owns is which controls it offers and where they post. The
// rules themselves (only an ACTIVE sender may be the default, one per tenant,
// cleared when the sender leaves `active`) are the server's and are tested
// there: a button that renders correctly proves nothing about a caller with a
// terminal.

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

import type { EmailSender, EmailSenderList } from "@/lib/types";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    apiDelete: vi.fn(),
    apiGet: vi.fn(),
    apiPost: vi.fn(),
  };
});
vi.mock("@/lib/api", () => api);
const toast = vi.fn();
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { EmailSendersCard } from "./email-senders-card";

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

function list(senders: EmailSender[], canAuthorize = true): EmailSenderList {
  return { senders, can_manage: true, can_authorize: canAuthorize };
}

beforeEach(() => {
  api.apiGet.mockReset();
  api.apiPost.mockReset();
  api.apiDelete.mockReset();
  toast.mockReset();
});
afterEach(() => cleanup());

describe("EmailSendersCard default sender", () => {
  it("offers Make default on an active sender and posts to the default route", async () => {
    api.apiGet.mockResolvedValue(list([sender({})]));
    api.apiPost.mockResolvedValue(sender({ is_default: true }));
    render(<EmailSendersCard />);

    const button = await screen.findByRole("button", { name: "Make default" });
    await act(async () => {
      fireEvent.click(button);
    });

    await waitFor(() =>
      expect(api.apiPost).toHaveBeenCalledWith("/email-senders/s-1/default")
    );
    expect(toast).toHaveBeenCalledWith({
      title: "Default sender set: hr@acme.example",
    });
  });

  it("labels the default sender and clears it with a DELETE", async () => {
    api.apiGet.mockResolvedValue(list([sender({ is_default: true })]));
    api.apiDelete.mockResolvedValue(sender({}));
    render(<EmailSendersCard />);

    expect(await screen.findByText("Default sender")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Stop using as default" })
      );
    });

    await waitFor(() =>
      expect(api.apiDelete).toHaveBeenCalledWith("/email-senders/s-1/default")
    );
  });

  it("never offers the default to a sender that is not active", async () => {
    api.apiGet.mockResolvedValue(
      list([
        sender({ id: "s-2", status: "disabled" }),
        sender({ id: "s-3", status: "pending_verification" }),
      ])
    );
    render(<EmailSendersCard />);

    await screen.findAllByText("hr@acme.example");
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
  });

  it("shows the default to a manager who cannot change it, with no control", async () => {
    api.apiGet.mockResolvedValue(list([sender({ is_default: true })], false));
    render(<EmailSendersCard />);

    expect(await screen.findByText("Default sender")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Stop using as default" })
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Make default" })).toBeNull();
  });

  it("names the failed act in a readable sentence", async () => {
    api.apiGet.mockResolvedValue(list([sender({ status: "pending_verification" })]));
    api.apiPost.mockRejectedValue(new Error("Sender not found"));
    render(<EmailSendersCard />);

    const approve = await screen.findByRole("button", { name: "Approve" });
    await act(async () => {
      fireEvent.click(approve);
    });

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Could not approve this sender" })
      )
    );
  });
});

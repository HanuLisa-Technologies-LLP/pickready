// @vitest-environment jsdom
//
// The recruiter's composer must reuse its idempotency token across retries.
// The server collapses duplicate sends on `client_token`; a panel that mints a
// fresh token per click turns a send whose response was lost into two
// messages the moment the recruiter presses Send again (vivekium release,
// Phase 6, audit P1-3.24).

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

const conversations = vi.hoisted(() => {
  let minted = 0;
  return {
    OFFLINE_REFRESH_MS: 15_000,
    attachmentUrl: vi.fn(),
    listMessages: vi.fn(async () => []),
    listMessagesBefore: vi.fn(async () => []),
    markConversationRead: vi.fn(async () => ({ ok: true })),
    newClientToken: vi.fn(() => `c-token-${++minted}`),
    openConversationStream: vi.fn(() => () => undefined),
    readableSize: (bytes: number) => `${bytes} bytes`,
    sendMessage: vi.fn(),
    uploadAttachment: vi.fn(),
  };
});
vi.mock("@/lib/conversations", () => conversations);

const toast = vi.fn();
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { ConversationPanel } from "./conversation-panel";

function message(id: string, body: string) {
  return {
    id,
    conversation_id: "conv-1",
    author_party: "recruiter" as const,
    author_user_id: "user-1",
    author_name: "Priya",
    channel: "chat" as const,
    body,
    created_at: "2026-09-24T10:00:00Z",
    delivery_status: "delivered",
    delivery_detail: null,
    attachments: [],
  };
}

async function type(text: string) {
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: text },
  });
}

async function clickSend() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
  });
}

beforeEach(() => {
  // jsdom implements no layout, so the panel's scroll-to-latest has nothing
  // to call.
  Element.prototype.scrollIntoView = vi.fn();
  conversations.sendMessage.mockReset();
  toast.mockReset();
});
afterEach(() => cleanup());

describe("ConversationPanel composer", () => {
  it("retries a failed send with the SAME client token", async () => {
    conversations.sendMessage
      .mockRejectedValueOnce(new Error("The connection dropped"))
      .mockResolvedValueOnce(message("m-1", "Hello Asha"));

    render(<ConversationPanel conversationId="conv-1" />);
    await type("Hello Asha");
    await clickSend();
    await waitFor(() => expect(toast).toHaveBeenCalled());
    await clickSend();

    await waitFor(() => expect(conversations.sendMessage).toHaveBeenCalledTimes(2));
    const [first, second] = conversations.sendMessage.mock.calls;
    expect(first[1]).toBe("Hello Asha");
    expect(second[1]).toBe("Hello Asha");
    expect(second[2]).toBe(first[2]);
  });

  it("uses a new token for the next message after a confirmed send", async () => {
    conversations.sendMessage
      .mockResolvedValueOnce(message("m-1", "First"))
      .mockResolvedValueOnce(message("m-2", "Second"));

    render(<ConversationPanel conversationId="conv-1" />);
    await type("First");
    await clickSend();
    await waitFor(() => expect(screen.getByText("First")).toBeTruthy());
    await type("Second");
    await clickSend();

    await waitFor(() => expect(conversations.sendMessage).toHaveBeenCalledTimes(2));
    const [first, second] = conversations.sendMessage.mock.calls;
    expect(second[2]).not.toBe(first[2]);
  });

  it("treats an edited draft as a different message", async () => {
    conversations.sendMessage
      .mockRejectedValueOnce(new Error("The connection dropped"))
      .mockResolvedValueOnce(message("m-1", "Hello Asha, corrected"));

    render(<ConversationPanel conversationId="conv-1" />);
    await type("Hello Asha");
    await clickSend();
    await waitFor(() => expect(toast).toHaveBeenCalled());
    await type("Hello Asha, corrected");
    await clickSend();

    await waitFor(() => expect(conversations.sendMessage).toHaveBeenCalledTimes(2));
    const [first, second] = conversations.sendMessage.mock.calls;
    expect(second[2]).not.toBe(first[2]);
  });
});

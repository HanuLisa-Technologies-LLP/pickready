// @vitest-environment jsdom
//
// The candidate's Messages page, against the real `lib/conversations` client
// with only the HTTP layer stubbed. What is pinned:
//
// * a retry of an unsent reply sends the SAME client token (a token minted per
//   attempt turned a retry after a lost response into a second message);
// * "Load earlier" pages on both halves of the keyset and prepends;
// * `?conversation=` opens the thread it names, and opening a thread marks it
//   read and tells the navigation badge.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const http = vi.hoisted(() => ({
  API_BASE: "/api/v1",
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiUpload: vi.fn(),
  tryRefresh: vi.fn(),
}));
vi.mock("@/lib/api", () => http);
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { UNREAD_CHANGED_EVENT, type Message } from "@/lib/conversations";
import CandidateMessagesPage from "./page";

const THREADS = [
  { id: "c1", subject: "Data Engineer", status: "open", company_name: "Acme", last_message_at: "2026-09-24T09:00:00+00:00", unread: 0 },
  { id: "c2", subject: "Platform Engineer", status: "open", company_name: "Globex", last_message_at: "2026-09-24T10:00:00+00:00", unread: 2 },
];

function message(id: string, minute: number, body = id): Message {
  return {
    id,
    conversation_id: "c1",
    author_party: "recruiter",
    author_user_id: null,
    author_name: null,
    body,
    channel: "chat",
    delivery_status: "sent",
    delivery_detail: null,
    created_at: `2026-09-24T10:${String(minute).padStart(2, "0")}:00+00:00`,
    attachments: [],
  };
}

function routeGets(pages: Record<string, Message[]>) {
  http.apiGet.mockImplementation((path: string) => {
    if (path === "/conversations/me") return Promise.resolve(THREADS);
    const key = Object.keys(pages).find((prefix) => path.startsWith(prefix));
    return Promise.resolve(key ? pages[key] : []);
  });
}

beforeEach(() => {
  nav.search = "";
  http.apiPost.mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("opening a thread", () => {
  it("opens the thread ?conversation= names, marks it read and tells the badge", async () => {
    nav.search = "conversation=c2";
    routeGets({ "/conversations/me/c2/messages": [message("m1", 1, "Hello from Globex")] });
    const announced = vi.fn();
    window.addEventListener(UNREAD_CHANGED_EVENT, announced);

    render(<CandidateMessagesPage />);
    expect(await screen.findByText("Hello from Globex")).toBeTruthy();
    await waitFor(() =>
      expect(http.apiPost).toHaveBeenCalledWith("/conversations/me/c2/read"),
    );
    await waitFor(() => expect(announced).toHaveBeenCalled());
    window.removeEventListener(UNREAD_CHANGED_EVENT, announced);
  });
});

describe("sending", () => {
  it("retries an unsent reply with the same token, and a new reply gets a new one", async () => {
    routeGets({ "/conversations/me/c1/messages": [message("m1", 1)] });
    render(<CandidateMessagesPage />);
    const box = await screen.findByLabelText("Reply");

    const sends = () =>
      http.apiPost.mock.calls.filter(([path]) =>
        String(path).endsWith("/c1/messages"),
      );
    http.apiPost.mockImplementation((path: string) =>
      path.endsWith("/c1/messages") && sends().length === 1
        ? Promise.reject(new Error("The network dropped"))
        : Promise.resolve(
            path.endsWith("/messages")
              ? { ...message(`sent-${sends().length}`, 30), author_party: "candidate" }
              : { ok: true },
          ),
    );

    fireEvent.change(box, { target: { value: "My notice period is thirty days" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(toast).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sends().length).toBe(2));

    const [first, second] = sends().map(([, body]) => body as { client_token: string });
    expect(second.client_token).toBe(first.client_token);

    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe(""));
    fireEvent.change(box, { target: { value: "And I can join on the first" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sends().length).toBe(3));
    const third = sends()[2][1] as { client_token: string };
    expect(third.client_token).not.toBe(first.client_token);
  });
});

describe("switching threads", () => {
  it("keeps each thread's draft to itself, and its token with it", async () => {
    routeGets({
      "/conversations/me/c1/messages": [message("m1", 1)],
      "/conversations/me/c2/messages": [{ ...message("m2", 2), conversation_id: "c2" }],
    });
    const sends = () =>
      http.apiPost.mock.calls.filter(([path]) => String(path).endsWith("/messages"));
    http.apiPost.mockImplementation((path: string) =>
      path.endsWith("/messages") && sends().length === 1
        ? Promise.reject(new Error("The network dropped"))
        : Promise.resolve(
            path.endsWith("/messages")
              ? { ...message("sent", 30), author_party: "candidate" }
              : { ok: true },
          ),
    );
    render(<CandidateMessagesPage />);
    const box = (await screen.findByLabelText("Reply")) as HTMLTextAreaElement;

    fireEvent.change(box, { target: { value: "For Acme only" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(toast).toHaveBeenCalled());

    // Globex's box is empty: Acme's words did not follow the selection.
    fireEvent.click(screen.getByRole("button", { name: /globex/i }));
    await waitFor(() => expect(box.value).toBe(""));

    // Back to Acme: the unsent draft is there, and a retry reuses its token.
    fireEvent.click(screen.getByRole("button", { name: /acme/i }));
    await waitFor(() => expect(box.value).toBe("For Acme only"));
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sends().length).toBe(2));
    const [first, second] = sends();
    expect(String(second[0])).toBe("/conversations/me/c1/messages");
    expect((second[1] as { client_token: string }).client_token).toBe(
      (first[1] as { client_token: string }).client_token,
    );
  });
});

describe("load earlier", () => {
  it("pages on the oldest message's timestamp AND id, and prepends", async () => {
    const newest = Array.from({ length: 50 }, (_, i) => message(`n${i}`, i + 5));
    http.apiGet.mockImplementation((path: string) => {
      if (path === "/conversations/me") return Promise.resolve(THREADS);
      if (path.includes("before=")) return Promise.resolve([message("old", 1, "The very first message")]);
      return Promise.resolve(newest);
    });
    render(<CandidateMessagesPage />);
    fireEvent.click(await screen.findByRole("button", { name: /load earlier/i }));
    expect(await screen.findByText("The very first message")).toBeTruthy();

    const earlier = http.apiGet.mock.calls
      .map(([path]) => String(path))
      .find((path) => path.includes("before="));
    const query = new URLSearchParams(earlier?.split("?")[1]);
    expect(query.get("before")).toBe(newest[0].created_at);
    expect(query.get("before_id")).toBe("n0");
    // A short page is the start of the thread: nothing further to load.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /load earlier/i })).toBeNull(),
    );
  });
});

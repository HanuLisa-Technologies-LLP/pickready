// The composer token, the history merge and the paging query.
//
// The load-bearing claim: a RETRY of the same words reuses the token the
// first attempt sent, because the server collapses a repeated token into the
// message it already stored. A token minted per attempt turned a retry after a
// lost response into a second, identical message.

import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  API_BASE: "/api/v1",
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiUpload: vi.fn(),
  tryRefresh: vi.fn(),
}));
vi.mock("./api", () => api);

import {
  ComposerToken,
  listMessagesBefore,
  listMyMessages,
  mergeMessages,
  type Message,
} from "./conversations";

afterEach(() => vi.clearAllMocks());

function message(id: string, createdAt: string): Message {
  return {
    id,
    conversation_id: "c1",
    author_party: "recruiter",
    author_user_id: null,
    author_name: null,
    body: id,
    channel: "chat",
    delivery_status: "sent",
    delivery_detail: null,
    created_at: createdAt,
    attachments: [],
  };
}

describe("ComposerToken", () => {
  it("keeps one token across retries of the same draft", () => {
    const composer = new ComposerToken();
    const first = composer.current();
    expect(composer.current()).toBe(first);
    expect(composer.current()).toBe(first);
  });

  it("rotates after a confirmed send or an edit, and never repeats", () => {
    const composer = new ComposerToken();
    const first = composer.current();
    composer.rotate();
    const second = composer.current();
    expect(second).not.toBe(first);
    expect(second.length).toBeLessThanOrEqual(64);
  });
});

describe("mergeMessages", () => {
  it("dedupes by id and orders by (created_at, id) the way the server pages", () => {
    const at = "2026-09-24T10:00:00+00:00";
    const merged = mergeMessages(
      [message("b", at), message("c", "2026-09-24T11:00:00+00:00")],
      [message("a", at), message("b", at)],
    );
    expect(merged.map((m) => m.id)).toEqual(["a", "b", "c"]);
  });

  it("takes the incoming copy of a message it already holds", () => {
    const at = "2026-09-24T10:00:00+00:00";
    const stale = { ...message("a", at), delivery_status: "pending" };
    const fresh = { ...message("a", at), delivery_status: "sent" };
    expect(mergeMessages([stale], [fresh])[0].delivery_status).toBe("sent");
  });
});

describe("paging", () => {
  it("sends both halves of the keyset when loading earlier", () => {
    api.apiGet.mockResolvedValue([]);
    void listMyMessages("c1", {
      created_at: "2026-09-24T10:00:00+00:00",
      id: "m1",
    });
    const url = api.apiGet.mock.calls[0][0] as string;
    const query = new URLSearchParams(url.split("?")[1]);
    expect(url.startsWith("/conversations/me/c1/messages?")).toBe(true);
    expect(query.get("before")).toBe("2026-09-24T10:00:00+00:00");
    expect(query.get("before_id")).toBe("m1");
    expect(query.get("limit")).toBe("50");
  });

  it("asks for the newest page with no cursor at all", () => {
    api.apiGet.mockResolvedValue([]);
    void listMyMessages("c1");
    const url = api.apiGet.mock.calls[0][0] as string;
    expect(url).toBe("/conversations/me/c1/messages?limit=50");
  });

  it("keeps the recruiter call compatible when no id is passed", () => {
    api.apiGet.mockResolvedValue([]);
    void listMessagesBefore("c1", "2026-09-24T10:00:00+00:00");
    const query = new URLSearchParams(
      (api.apiGet.mock.calls[0][0] as string).split("?")[1],
    );
    expect(query.get("before_id")).toBeNull();
  });
});

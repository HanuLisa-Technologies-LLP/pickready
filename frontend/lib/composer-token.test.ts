import { describe, expect, it } from "vitest";

import { createComposerToken } from "./composer-token";

function counter() {
  let n = 0;
  return () => `c-token-${++n}`;
}

describe("createComposerToken", () => {
  it("keeps one token for a draft however many times it is read", () => {
    const token = createComposerToken(counter());
    expect(token.current()).toBe("c-token-1");
    expect(token.current()).toBe("c-token-1");
  });

  it("moves to a new token only when rotated", () => {
    const token = createComposerToken(counter());
    const first = token.current();
    token.rotate();
    expect(token.current()).not.toBe(first);
    expect(token.current()).toBe("c-token-2");
  });

  it("mints tokens the server accepts by default", () => {
    // The API requires 8 to 64 characters (SendMessageIn in api/conversations.py).
    const value = createComposerToken().current();
    expect(value.length).toBeGreaterThanOrEqual(8);
    expect(value.length).toBeLessThanOrEqual(64);
  });
});

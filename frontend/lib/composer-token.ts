/**
 * One idempotency token per COMPOSED MESSAGE, not per attempt to send it.
 *
 * The conversations API collapses duplicate sends on `client_token`, which is
 * only worth anything if a retry carries the same token as the attempt it is
 * retrying. Both composers used to call `newClientToken()` inside the send
 * handler, so every click minted a fresh one: a send whose response was lost
 * and was then retried arrived as two distinct messages, and the server did
 * exactly what it was told. The server was right; the client was lying to it
 * about which sends were the same send.
 *
 * The rule is therefore about the DRAFT:
 *   - the token is minted when a draft starts and survives any number of
 *     failed attempts to send that draft;
 *   - it rotates after a CONFIRMED send, so the next message is a new one;
 *   - it rotates when the draft is EDITED, because different words are a
 *     different message even if the previous send secretly succeeded.
 */
import * as React from "react";

import { newClientToken } from "@/lib/conversations";

export interface ComposerToken {
  /** The token for the draft as it stands now. Stable across retries. */
  current(): string;
  /** Start a new draft: after a confirmed send, or when the text changes. */
  rotate(): void;
}

/** The pure half, so the rule is testable without React. */
export function createComposerToken(
  mint: () => string = newClientToken
): ComposerToken {
  let token = mint();
  return {
    current: () => token,
    rotate: () => {
      token = mint();
    },
  };
}

/** One composer's token, held for the component's lifetime. */
export function useComposerToken(): ComposerToken {
  // A lazy state initialiser, never read-and-assign on a ref during render:
  // the holder is created once and its identity never changes, while the
  // token inside it moves only through `rotate`.
  const [holder] = React.useState(() => createComposerToken());
  return holder;
}

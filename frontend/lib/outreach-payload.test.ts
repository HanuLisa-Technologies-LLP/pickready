import { describe, expect, it } from "vitest";

import { buildOutreachComposePayload } from "./outreach-payload";

describe("buildOutreachComposePayload", () => {
  it("preserves selected job-candidate link ids", () => {
    const payload = buildOutreachComposePayload(
      "job-1",
      [{ link_id: "link-1" }, { link_id: "link-2" }],
      "ai",
    );
    expect(payload.link_ids).toEqual(["link-1", "link-2"]);
  });

  it("blocks an empty recipient request before it reaches FastAPI", () => {
    expect(() => buildOutreachComposePayload("job-1", [], "ai")).toThrow(
      "Select at least one candidate",
    );
  });
});

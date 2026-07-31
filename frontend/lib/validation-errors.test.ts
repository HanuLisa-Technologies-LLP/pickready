import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { apiErrorMessage } from "./validation-errors";

describe("apiErrorMessage", () => {
  it("renders FastAPI 422 field paths and reasons", () => {
    const error = new ApiError(422, {
      detail: [
        {
          loc: ["body", "jd", "reportees"],
          msg: "Input should be a valid integer",
        },
      ],
    });
    expect(apiErrorMessage(error)).toBe(
      "jd.reportees: Input should be a valid integer",
    );
  });
});

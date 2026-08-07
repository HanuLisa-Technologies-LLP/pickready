// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OptionalProctoringConsent } from "./optional-proctoring-consent";

afterEach(cleanup);

describe("optional proctoring consent", () => {
  it("is absent when the feature flag is off", () => {
    render(<OptionalProctoringConsent enabled={false} />);
    expect(screen.queryByTestId("optional-proctoring-consent")).toBeNull();
  });

  it("allows decline with explicit zero scoring penalty", () => {
    render(<OptionalProctoringConsent enabled />);
    expect(screen.getByText(/zero effect on questions, scoring, ranking/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /continue without/i }));
    expect(screen.queryByTestId("optional-proctoring-consent")).toBeNull();
  });

  it("requests video only and never audio", async () => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    const getDisplayMedia = vi.fn().mockResolvedValue(stream);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia, getDisplayMedia },
    });
    render(<OptionalProctoringConsent enabled />);
    fireEvent.click(screen.getByRole("button", { name: /allow optional/i }));
    await screen.findByText(/optional identity and screen capture enabled/i);
    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: false }));
    expect(getDisplayMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: false }));
  });
});

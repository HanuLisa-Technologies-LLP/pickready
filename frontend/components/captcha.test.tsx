// @vitest-environment jsdom
//
// The security check component draws nothing and judges nothing: it shows the
// server's image, sends what was typed, and hands the server's single-use
// proof to its parent. Pinned here: the purpose travels with both calls, an
// empty field is refused before any request, every verification (right or
// wrong) loads a fresh image, and the server's refusal is what the person
// reads.

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiPost: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});

import { ApiError } from "@/lib/api";
import {
  CAPTCHA_EMPTY_MESSAGE,
  Captcha,
  CaptchaError,
  type CaptchaHandle,
} from "./captcha";

let issued = 0;

function serve(verify: (body: unknown) => unknown) {
  issued = 0;
  http.apiPost.mockImplementation(async (path: string, body: unknown) => {
    if (path === "/auth/captcha/challenge") {
      issued += 1;
      return {
        challenge_id: `c${issued}`,
        image: `data:image/svg+xml;base64,${btoa(`<svg id="${issued}"/>`)}`,
        expires_in: 300,
      };
    }
    if (path === "/auth/captcha/verify") return verify(body);
    throw new Error(`unexpected POST ${path}`);
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function mount() {
  const ref = React.createRef<CaptchaHandle>();
  render(<Captcha ref={ref} purpose="company_login" />);
  await screen.findByAltText(/security check characters/i);
  return ref;
}

describe("Captcha", () => {
  it("asks for a challenge for its own purpose and shows the server's image", async () => {
    serve(() => ({ captcha_proof: "p" }));
    await mount();
    expect(http.apiPost).toHaveBeenCalledWith("/auth/captcha/challenge", { purpose: "company_login" });
    const image = screen.getByAltText(/security check characters/i) as HTMLImageElement;
    expect(image.src.startsWith("data:image/svg+xml;base64,")).toBe(true);
  });

  it("refuses an empty answer without asking the server", async () => {
    serve(() => ({ captcha_proof: "p" }));
    const ref = await mount();
    expect(ref.current!.hasAnswer()).toBe(false);
    await expect(ref.current!.prove()).rejects.toThrow(CAPTCHA_EMPTY_MESSAGE);
    expect(http.apiPost).not.toHaveBeenCalledWith("/auth/captcha/verify", expect.anything());
  });

  it("returns the server's proof and loads a fresh image, because the old one is spent", async () => {
    serve(() => ({ captcha_proof: "proof-ok" }));
    const ref = await mount();
    fireEvent.change(screen.getByLabelText("Security check"), { target: { value: " k7m2px " } });
    expect(ref.current!.hasAnswer()).toBe(true);
    let proof = "";
    await act(async () => {
      proof = await ref.current!.prove();
    });
    expect(proof).toBe("proof-ok");
    expect(http.apiPost).toHaveBeenCalledWith("/auth/captcha/verify", {
      challenge_id: "c1",
      answer: "k7m2px",
      purpose: "company_login",
    });
    await waitFor(() => expect(issued).toBe(2));
    expect((screen.getByLabelText("Security check") as HTMLInputElement).value).toBe("");
  });

  it("passes the server's refusal on as a CaptchaError, and reloads", async () => {
    serve(() => {
      throw new ApiError(400, { detail: "Those characters did not match. Try the new image." });
    });
    const ref = await mount();
    fireEvent.change(screen.getByLabelText("Security check"), { target: { value: "wrong1" } });
    let caught: unknown;
    await act(async () => {
      caught = await ref.current!.prove().catch((error) => error);
    });
    expect(caught).toBeInstanceOf(CaptchaError);
    expect((caught as Error).message).toBe("Those characters did not match. Try the new image.");
    await waitFor(() => expect(issued).toBe(2));
  });

  it("offers a new image on request", async () => {
    serve(() => ({ captcha_proof: "p" }));
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Show new characters" }));
    await waitFor(() => expect(issued).toBe(2));
  });
});

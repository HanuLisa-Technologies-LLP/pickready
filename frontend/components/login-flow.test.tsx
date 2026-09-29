// @vitest-environment jsdom
//
// The sign-in pages (auth spec 6.4, 7). Pinned: the company page offers no
// Google and sends a `company_login` proof; the candidate page offers Google
// and sends `candidate_login`; the security check is verified BEFORE Firebase
// is asked; and the exchange carries the proof and its purpose.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiPost: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
const firebaseSdk = vi.hoisted(() => ({
  signInWithEmailAndPassword: vi.fn(),
  signInWithPopup: vi.fn(),
}));
vi.mock("firebase/auth", () => firebaseSdk);
vi.mock("@/lib/firebase", () => ({
  firebaseAuth: { name: "test-auth" },
  createCandidateGoogleProvider: () => ({}),
}));
const navigation = vi.hoisted(() => ({ replace: vi.fn(), params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => navigation.params,
}));
const auth = vi.hoisted(() => ({ setSession: vi.fn() }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ setSession: auth.setSession }),
  homePathForRole: (role: string) => (role === "candidate" ? "/portal" : "/org"),
}));

import { LoginFlow, LoginPageFlow } from "./login-flow";

const order: string[] = [];

function serve() {
  http.apiPost.mockImplementation(async (path: string) => {
    order.push(path);
    if (path === "/auth/captcha/challenge") {
      return { challenge_id: "c1", image: "data:image/svg+xml;base64,PHN2Zy8+", expires_in: 300 };
    }
    if (path === "/auth/captcha/verify") return { captcha_proof: "proof-1" };
    if (path === "/auth/firebase/session") {
      return { user: { id: "u1", role: "recruiter" }, capabilities: [] };
    }
    throw new Error(`unexpected POST ${path}`);
  });
  firebaseSdk.signInWithEmailAndPassword.mockImplementation(async () => {
    order.push("firebase");
    return { user: { getIdToken: async () => "id-token" } };
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  order.length = 0;
  navigation.params = new URLSearchParams();
});

async function signIn() {
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "a@b.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Secret-123" } });
  fireEvent.change(screen.getByLabelText("Security check"), { target: { value: "k7m2px" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(auth.setSession).toHaveBeenCalled());
}

describe("the company sign-in page", () => {
  it("offers no Google and signs in with a company_login proof", async () => {
    serve();
    render(<LoginFlow surface="company" title="Sign in to your company workspace" />);
    await screen.findByAltText(/security check characters/i);
    expect(screen.queryByRole("button", { name: /google/i })).toBeNull();
    expect(http.apiPost).toHaveBeenCalledWith("/auth/captcha/challenge", { purpose: "company_login" });

    await signIn();
    // The check is verified before Firebase is asked for anything.
    expect(order.indexOf("/auth/captcha/verify")).toBeLessThan(order.indexOf("firebase"));
    expect(http.apiPost).toHaveBeenCalledWith("/auth/firebase/session", {
      id_token: "id-token",
      captcha_proof: "proof-1",
      captcha_purpose: "company_login",
    });
    expect(navigation.replace).toHaveBeenCalledWith("/org");
  });
});

describe("the candidate sign-in page", () => {
  it("offers Google, links to the company page, and signs in with a candidate_login proof", async () => {
    serve();
    render(<LoginPageFlow />);
    await screen.findByAltText(/security check characters/i);
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Company login" }).getAttribute("href")).toBe(
      "/company/login",
    );
    await signIn();
    expect(http.apiPost).toHaveBeenCalledWith(
      "/auth/firebase/session",
      expect.objectContaining({ captcha_purpose: "candidate_login" }),
    );
  });

  it("asks for the security check before opening the Google popup", async () => {
    serve();
    render(<LoginPageFlow />);
    await screen.findByAltText(/security check characters/i);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    expect(await screen.findByText(/type the characters shown/i)).toBeTruthy();
    expect(firebaseSdk.signInWithPopup).not.toHaveBeenCalled();
  });

  it("uses the Provider purpose behind ?portal=owner", async () => {
    serve();
    navigation.params = new URLSearchParams("portal=owner");
    render(<LoginPageFlow />);
    await screen.findByAltText(/security check characters/i);
    expect(http.apiPost).toHaveBeenCalledWith("/auth/captcha/challenge", { purpose: "provider_login" });
  });
});

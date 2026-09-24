// @vitest-environment jsdom
//
// The three My Profile cards added for the candidate's own records:
// verification documents, keep my profile, and my consents. What is pinned:
//
// * documents: EVERY accepted type renders, empty ones included; an upload
//   posts the type and the file; a full type refuses further uploads; the
//   server's refusal is shown in its own words;
// * keep my profile: the server's message and dates are what renders, reading
//   renews nothing, and the button posts the renewal then re-reads the state;
// * my consents: the whole catalogue with given and not given, and the
//   history's verbatim wording, with no version or digest on screen.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiUpload: vi.fn(),
  apiDelete: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { ApiError } from "@/lib/api";
import { BgvDocumentsCard, type BgvDocumentsOut } from "./bgv-documents-card";
import { ConsentRenewalCard } from "./consent-renewal-card";
import { ConsentHistoryCard } from "./consent-history-card";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function documents(overrides: Partial<BgvDocumentsOut> = {}): BgvDocumentsOut {
  return {
    documents: [],
    accepted_types: [
      { key: "academic_certificate", label: "Academic certificate" },
      { key: "address_proof", label: "Address proof" },
    ],
    upload_hint: "PDF, JPG or PNG, up to 10 MB.",
    max_per_type: 1,
    ...overrides,
  };
}

describe("verification documents", () => {
  it("shows every accepted type, empty ones included", async () => {
    http.apiGet.mockResolvedValue(documents());
    render(<BgvDocumentsCard />);
    expect(await screen.findByText("Academic certificate")).toBeTruthy();
    expect(screen.getByText("Address proof")).toBeTruthy();
    expect(screen.getAllByText("Not added yet.")).toHaveLength(2);
    expect(screen.getByText("PDF, JPG or PNG, up to 10 MB.")).toBeTruthy();
  });

  it("uploads with the document type, and refuses a full type", async () => {
    http.apiGet.mockResolvedValue(documents());
    http.apiUpload.mockResolvedValue(
      documents({
        documents: [
          {
            id: "d1",
            document_type: "address_proof",
            document_label: "Address proof",
            original_filename: "utility-bill.pdf",
            mime_type: "application/pdf",
            size_bytes: 2048,
            uploaded_at: "2026-09-24T10:00:00+00:00",
          },
        ],
      }),
    );
    render(<BgvDocumentsCard />);
    const input = (await screen.findByLabelText("Add Address proof")) as HTMLInputElement;
    const file = new File(["%PDF"], "utility-bill.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(http.apiUpload).toHaveBeenCalled());
    const [path, form] = http.apiUpload.mock.calls[0];
    expect(path).toBe("/bgv/me/documents");
    expect((form as FormData).get("document_type")).toBe("address_proof");
    expect(((form as FormData).get("file") as File).name).toBe("utility-bill.pdf");

    expect(await screen.findByText(/utility-bill\.pdf/)).toBeTruthy();
    expect((screen.getByLabelText("Add Address proof") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/this type is full/i)).toBeTruthy();
  });

  it("shows the server's refusal in its own words", async () => {
    http.apiGet.mockResolvedValue(documents());
    http.apiUpload.mockRejectedValue(
      new ApiError(422, { detail: "That file type is not accepted. Use PDF, JPG or PNG." }),
    );
    render(<BgvDocumentsCard />);
    const input = await screen.findByLabelText("Add Academic certificate");
    fireEvent.change(input, {
      target: { files: [new File(["x"], "degree.exe", { type: "application/octet-stream" })] },
    });
    expect(await screen.findByText(/that file type is not accepted/i)).toBeTruthy();
  });
});

describe("keep my profile", () => {
  const renewal = {
    consented_at: "2026-03-01T00:00:00+00:00",
    renewal_due_at: "2026-09-01T00:00:00+00:00",
    stage: "reminder_due",
    renewal_needed: true,
    message: "Your profile is due for renewal. Confirm below to keep it.",
  };

  it("renders the server's message and renews only when asked", async () => {
    http.apiGet.mockResolvedValue(renewal);
    http.apiPost.mockResolvedValue({
      renewed: true,
      renewed_at: "2026-09-24T10:00:00+00:00",
      message: "Thank you. Your profile will be kept.",
    });
    render(<ConsentRenewalCard />);
    expect(await screen.findByText(renewal.message)).toBeTruthy();
    expect(http.apiPost).not.toHaveBeenCalled();

    http.apiGet.mockResolvedValue({ ...renewal, renewal_needed: false, message: "Your profile is active." });
    fireEvent.click(screen.getByRole("button", { name: "Keep my profile" }));
    await waitFor(() => expect(http.apiPost).toHaveBeenCalledWith("/portal/me/consent/renew"));
    expect(await screen.findByText("Thank you. Your profile will be kept.")).toBeTruthy();
    expect(await screen.findByText("Your profile is active.")).toBeTruthy();
    expect(http.apiGet).toHaveBeenCalledTimes(2);
  });
});

describe("my consents", () => {
  it("shows the whole catalogue and the verbatim history, with no version or digest", async () => {
    http.apiGet.mockResolvedValue({
      items: [
        {
          key: "privacy",
          stage: "A",
          text: "I agree to the privacy policy.",
          version: "2026-09-01",
          required: true,
          consented_at: "2026-09-02T00:00:00+00:00",
          consented_version: "2026-08-01",
          wording_current: false,
        },
        {
          key: "cross_employer_reuse",
          stage: "B",
          text: "Evidence may be reused across employers.",
          version: "2026-09-01",
          required: false,
          consented_at: null,
          consented_version: null,
          wording_current: false,
        },
      ],
      history: [
        {
          key: "privacy",
          stage: "A",
          source: "registration",
          version: "2026-08-01",
          text: "I agreed to the old privacy wording.",
          text_sha256: "abc123digest",
          recorded_at: "2026-09-02T00:00:00+00:00",
        },
        {
          key: "privacy",
          stage: "A",
          source: "registration",
          version: null,
          text: null,
          text_sha256: null,
          recorded_at: "2026-01-02T00:00:00+00:00",
        },
      ],
    });
    const { container } = render(<ConsentHistoryCard />);
    expect(await screen.findByText("I agree to the privacy policy.")).toBeTruthy();
    expect(screen.getByText("Evidence may be reused across employers.")).toBeTruthy();
    expect(screen.getByText(/not agreed/i)).toBeTruthy();
    expect(screen.getByText(/wording has changed since you agreed/i)).toBeTruthy();
    expect(screen.getByText("I agreed to the old privacy wording.")).toBeTruthy();
    expect(screen.getByText(/exact wording was not recorded/i)).toBeTruthy();
    expect(container.textContent).not.toContain("abc123digest");
    expect(container.textContent).not.toContain("2026-08-01");
  });

  it("says a failed load rather than showing an empty record", async () => {
    http.apiGet.mockRejectedValue(new ApiError(500, { detail: "Internal error" }));
    render(<ConsentHistoryCard />);
    expect(await screen.findByText(/could not be loaded/i)).toBeTruthy();
  });
});

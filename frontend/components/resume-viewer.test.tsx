// @vitest-environment jsdom
//
// Resume viewing and download (bug fixes 11 and 12, 2026-08-09).
//
// Resumes live in private storage, so the only way to read one is the
// authenticated profile endpoint. A viewer handed no `profileId` cannot ask for
// anything, which is what produced both "resumes cannot be viewed or
// downloaded" and the "profile reference is missing" panel on Word documents.
//
// The second half is the routing decision: a private object name carries no
// extension, so the recorded MIME type has to decide whether a document goes to
// the server-side DOCX renderer or to an iframe. Getting that wrong renders a
// blank frame, which reads to a recruiter as a broken resume.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ResumeViewer,
  describeResumeUrl,
  kindFromMimeType,
  resumeTabUrl,
} from "./resume-viewer";

afterEach(cleanup);

const DOCX =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
// An `s3://` object reference. A browser cannot fetch one, which is the point:
// every read goes through the authenticated, tenant-scoped download endpoint.
const PRIVATE_OBJECT = "s3://readypick-staging-private/resumes/abc123";
const PROFILE_ID = "9f1c2e40-0000-4000-8000-00000000abcd";

describe("preview routing", () => {
  it("routes a Word MIME type to the server-side renderer", () => {
    expect(kindFromMimeType(DOCX)).toBe("word");
    expect(kindFromMimeType("application/msword")).toBe("word");
  });

  it("routes a PDF to the browser's own viewer", () => {
    expect(kindFromMimeType("application/pdf")).toBe("framable");
    expect(kindFromMimeType("application/pdf; charset=binary")).toBe("framable");
  });

  it("decides nothing when no MIME type was recorded", () => {
    // Null, not a guess: the filename is then the only signal there is.
    expect(kindFromMimeType(null)).toBeNull();
    expect(kindFromMimeType("")).toBeNull();
  });

  it("still trusts a real filename extension over the MIME type", () => {
    expect(describeResumeUrl("asha-rao.docx").kind).toBe("word");
    expect(describeResumeUrl("asha-rao.pdf").kind).toBe("framable");
  });
});

describe("resume viewer", () => {
  it("fetches the DOCX preview through the profile endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, blob: async () => new Blob(["<p>cv</p>"]) });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: () => "blob:preview",
      revokeObjectURL: () => {},
    });

    render(
      <ResumeViewer
        open
        onOpenChange={() => {}}
        resumeUrl={PRIVATE_OBJECT}
        profileId={PROFILE_ID}
        resumeFileName="asha-rao.docx"
        resumeMimeType={DOCX}
        candidateName="Asha Rao"
      />
    );

    expect(fetchMock).toHaveBeenCalled();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain(`/candidates/profiles/${PROFILE_ID}/resume-preview`);
    // The endpoint is authenticated; a fetch without the session cookie is a
    // 401 that surfaces as "preview could not be loaded".
    expect(options.credentials).toBe("include");
    vi.unstubAllGlobals();
  });

  it("routes an extension-less private object by its MIME type", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, blob: async () => new Blob(["<p>cv</p>"]) });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: () => "blob:preview",
      revokeObjectURL: () => {},
    });

    render(
      <ResumeViewer
        open
        onOpenChange={() => {}}
        resumeUrl={PRIVATE_OBJECT}
        profileId={PROFILE_ID}
        resumeFileName={null}
        resumeMimeType={DOCX}
        candidateName="Asha Rao"
      />
    );

    // Without the MIME type this fell through to `resume-file` and an iframe,
    // which renders nothing for a .docx.
    expect(fetchMock.mock.calls[0][0]).toContain("resume-preview");
    vi.unstubAllGlobals();
  });

  it("downloads through the profile endpoint, never the raw storage scheme", () => {
    render(
      <ResumeViewer
        open
        onOpenChange={() => {}}
        resumeUrl={PRIVATE_OBJECT}
        profileId={PROFILE_ID}
        resumeFileName="asha-rao.pdf"
        resumeMimeType="application/pdf"
        candidateName="Asha Rao"
      />
    );
    const link = screen.getAllByRole("link", { name: /download/i })[0];
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain(`/candidates/profiles/${PROFILE_ID}/resume-file`);
    expect(href).toContain("download=true");
    // Neither scheme may ever reach an href. `gs://` is checked too because rows
// written before the AWS migration still carry it.
    expect(href.startsWith("s3://")).toBe(false);
    expect(href.startsWith("gs://")).toBe(false);
  });

  it("shows the profile-reference panel only when there is genuinely no profile", () => {
    render(
      <ResumeViewer
        open
        onOpenChange={() => {}}
        resumeUrl={PRIVATE_OBJECT}
        profileId={null}
        resumeFileName="asha-rao.docx"
        resumeMimeType={DOCX}
        candidateName="Asha Rao"
      />
    );
    // This panel is correct for a genuinely profile-less row; the bug was that
    // the recruiter portal reached it for every candidate.
    expect(screen.getByText(/profile reference is missing/i)).toBeTruthy();
  });
});

// ── The Resume Link column opens a TAB (vivekium feature 3, column 6) ────────
//
// The brief asks for a direct tap that opens the resume in a new tab, and the
// href has to be something a browser can navigate to on its own. The row no
// longer carries a storage URL at all, so the only inputs are the profile id
// and the two descriptive fields; everything this builds has to be the
// authorized proxy, never an object reference.
describe("resumeTabUrl", () => {
  it("sends a Word document to the server-side renderer", () => {
    // A tab handed raw DOCX bytes silently turns the tap into a download,
    // which is why the endpoint differs by format rather than by preference.
    expect(
      resumeTabUrl({ profileId: PROFILE_ID, resumeMimeType: DOCX })
    ).toContain(`/candidates/profiles/${PROFILE_ID}/resume-preview`);
  });

  it("sends a PDF to the streaming route the browser can render", () => {
    expect(
      resumeTabUrl({ profileId: PROFILE_ID, resumeMimeType: "application/pdf" })
    ).toContain(`/candidates/profiles/${PROFILE_ID}/resume-file`);
  });

  it("trusts a real filename extension over the recorded MIME type", () => {
    expect(
      resumeTabUrl({
        profileId: PROFILE_ID,
        resumeFileName: "asha-rao.docx",
        resumeMimeType: "application/pdf",
      })
    ).toContain("resume-preview");
  });

  it("never emits a storage reference", () => {
    const url = resumeTabUrl({
      profileId: PROFILE_ID,
      resumeFileName: "asha-rao.pdf",
    });
    expect(url).not.toContain("s3://");
    expect(url).not.toContain(PRIVATE_OBJECT);
    expect(url?.startsWith("/api/")).toBe(true);
  });

  it("has nothing to open without a profile", () => {
    // The row that has no resume. The caller renders a disabled control
    // rather than an href that 404s.
    expect(resumeTabUrl({ profileId: null })).toBeNull();
    expect(resumeTabUrl({ profileId: undefined })).toBeNull();
  });
});

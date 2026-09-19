"""The resume upload's CONTENT check, which nothing had ever asserted.

WHAT IS MISSING FROM `test_resume_upload.py`, AND WHY IT MATTERS
----------------------------------------------------------------
That file covers the three checks a reviewer thinks of first: the extension
allowlist, the declared content type, and the size ceiling. All three read
metadata the UPLOADER CHOSE. `read_validated_resume` has a fourth check,
`_assert_document_signature`, which is the only one that reads the BYTES, and
it had no test at all.

The gap was invisible because the one test that comes closest,
`test_upload_accepts_docx_by_extension`, happens to send `PK\\x03\\x04 docx` as
its payload. So it passes the signature check by accident while asserting
something else entirely (that a browser's `application/octet-stream` does not
refuse a real .docx). Delete the signature check today and every existing
resume test still passes.

WHAT THAT WOULD COST
--------------------
The uploaded bytes are stored, parsed, embedded, and put in front of a model
that grades a person. A file whose extension lies about its content is the
ordinary shape of an upload attack: the name and the declared MIME are both
attacker-controlled and cost nothing to forge, and the first four bytes are
the cheapest thing in the pipeline that is not. These tests pin the refusal in
BOTH directions, because a signature check that refused genuine documents
would break every candidate's application while looking like a storage fault.

No database, no S3, no network: `read_validated_resume` reads an UploadFile and
raises. That is deliberate -- the check must hold before anything is persisted.
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.services.resume_storage import (
    MAX_RESUME_BYTES,
    read_validated_resume,
)

#: The real first bytes of each accepted format. A PDF is identified by the
#: `%PDF-` header; a .docx is a ZIP container, so it opens with the local file
#: header "PK".
PDF_MAGIC = b"%PDF-1.7\n1 0 obj\n"
DOCX_MAGIC = b"PK\x03\x04\x14\x00\x06\x00"

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _upload(
    data: bytes,
    filename: str,
    content_type: str = PDF_MIME,
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


# ── A name that lies about the bytes ────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "data"),
    [
        # A Windows executable. The one a filter exists to stop.
        ("pe_executable", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64),
        # A shell script. Harmless to store, not harmless to hand a parser.
        ("shell_script", b"#!/bin/sh\nrm -rf /\n"),
        # HTML, which a careless previewer would render.
        ("html", b"<html><script>alert(1)</script></html>"),
        # A ZIP, which is a valid .docx body and an invalid .pdf one. The pair
        # with the docx case below is what proves the check reads the EXTENSION
        # it was given rather than accepting any recognised format.
        ("zip_container", DOCX_MAGIC + b"rest of a docx"),
        # Whitespace before the header. `%PDF-` must be at offset zero; a
        # `strip()`-then-check implementation would admit this.
        ("leading_whitespace", b"   " + PDF_MAGIC),
        # The magic bytes present but not first. A `b"%PDF-" in data` check
        # would admit this, and that is the obvious wrong implementation.
        ("magic_not_at_the_start", b"GIF89a" + PDF_MAGIC),
    ],
)
def test_a_file_named_pdf_whose_bytes_are_not_a_pdf_is_refused(
    label: str, data: bytes
) -> None:
    """Both attacker-controlled strings say PDF; only the bytes disagree.

    The filename and the Content-Type header are supplied by the client and
    cost nothing to set correctly, so a validator that reads only those two has
    validated nothing. This sends the perfect metadata every time and varies
    only the payload.
    """
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(data, f"{label}.pdf", PDF_MIME)))
    assert refused.value.status_code == 422
    # The candidate is told what is wrong with their file, not "Request failed".
    assert "PDF" in refused.value.detail["message"]


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("pe_executable", b"MZ\x90\x00\x03\x00\x00\x00"),
        # A real PDF renamed .docx. The mirror of the zip_container case above:
        # a recognised document format is still refused when it is not the one
        # the extension claims.
        ("pdf_renamed_docx", PDF_MAGIC),
        ("plain_text", b"Dear hiring manager, please see my resume below."),
        ("rtf", b"{\\rtf1\\ansi\\deff0 resume}"),
        # Legacy binary .doc (OLE compound file). A different Word format is
        # not this Word format: python-docx cannot open it.
        ("legacy_doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    ],
)
def test_a_file_named_docx_whose_bytes_are_not_a_zip_is_refused(
    label: str, data: bytes
) -> None:
    """A .docx is a ZIP container, so its first two bytes are always PK."""
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(data, f"{label}.docx", DOCX_MIME)))
    assert refused.value.status_code == 422
    assert "DOCX" in refused.value.detail["message"]


def test_a_single_byte_file_does_not_crash_the_signature_check() -> None:
    """The .docx check slices `data[:2]`, which is one byte long here.

    Asserted because slicing does not raise but `data[1]` would, and a
    validator that 500s on a one-byte upload is a validator that answers a
    probe with a stack trace instead of a refusal.
    """
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(b"P", "tiny.docx", DOCX_MIME)))
    assert refused.value.status_code == 422


# ── The direction that matters more: genuine files still work ───────────────


def test_a_real_pdf_is_accepted_and_its_mime_type_is_ours_not_theirs() -> None:
    """THE HALF THAT WOULD BREAK EVERY CANDIDATE.

    A signature check tightened until it refused real documents would reject
    every application in the product, and the failure would read as a storage
    outage rather than as a validator. It is asserted alongside the refusals so
    one cannot be made stricter without the other failing.

    The returned MIME is also asserted because the stored object's content type
    must be the one WE derived from the extension, never the header the client
    sent: an uploader who declares `text/html` on a real PDF would otherwise
    get a stored object that a browser renders as a page.
    """
    data, filename, mime_type = _run(
        read_validated_resume(
            _upload(PDF_MAGIC + b"%%EOF", "Priya_Raman_CV.pdf", PDF_MIME)
        )
    )
    assert data.startswith(b"%PDF-")
    assert filename == "Priya_Raman_CV.pdf"
    assert mime_type == PDF_MIME


def test_a_real_docx_is_accepted() -> None:
    data, filename, mime_type = _run(
        read_validated_resume(
            _upload(DOCX_MAGIC + b"[Content_Types].xml", "cv.docx", DOCX_MIME)
        )
    )
    assert data.startswith(b"PK")
    assert filename == "cv.docx"
    assert mime_type == DOCX_MIME


def test_the_stored_mime_type_is_derived_and_never_the_clients_header() -> None:
    """`application/octet-stream` is on the allowlist because that is what some
    browsers send for a .docx, so a real one must not be refused for it.

    What must NOT happen is that the header is then carried through to the
    stored object: the object's content type decides what a browser does when
    the presigned URL is opened, and letting the uploader choose it turns a
    resume bucket into a place to host a chosen content type.
    """
    _, _, mime_type = _run(
        read_validated_resume(
            _upload(DOCX_MAGIC, "cv.docx", "application/octet-stream")
        )
    )
    assert mime_type == DOCX_MIME, "the uploader's header reached the stored object"


def test_an_uppercase_extension_is_accepted_on_a_real_pdf() -> None:
    """A phone camera app that writes `CV.PDF` must not be refused. The
    extension is lowercased before the allowlist is consulted, and this is the
    regression that a hand-rolled `endswith('.pdf')` would reintroduce."""
    _, filename, mime_type = _run(
        read_validated_resume(_upload(PDF_MAGIC, "CV.PDF", PDF_MIME))
    )
    # The stored filename keeps the candidate's own capitalisation.
    assert filename == "CV.PDF"
    assert mime_type == PDF_MIME


# ── The filename itself is attacker-controlled ──────────────────────────────


def test_a_traversal_filename_is_reduced_to_its_basename() -> None:
    """The uploaded name reaches object metadata and a Content-Disposition
    header. A path in it is a path somebody eventually joins onto a directory.

    Only forward slashes are exercised: `os.path.basename` is `ntpath` on
    Windows and `posixpath` on Linux, and asserting the backslash case here
    would make the test pass or fail by platform rather than by behaviour.
    """
    _, filename, _ = _run(
        read_validated_resume(
            _upload(PDF_MAGIC, "../../../etc/passwd.pdf", PDF_MIME)
        )
    )
    assert filename == "passwd.pdf"
    assert "/" not in filename and ".." not in filename


def test_a_filename_that_is_only_a_path_is_refused_rather_than_stored_empty() -> None:
    """`basename("../")` is the empty string. Stored, it would be an object
    with a blank download name; refused, the candidate is told to rename."""
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(PDF_MAGIC, "some/dir/", PDF_MIME)))
    assert refused.value.status_code == 422


def test_an_absurdly_long_filename_is_refused() -> None:
    """255 bytes is the limit on every filesystem this product's objects are
    ever written to. Storing a longer one fails at the far end of a background
    task, where nobody is watching."""
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(PDF_MAGIC, "a" * 300 + ".pdf", PDF_MIME)))
    assert refused.value.status_code == 422


# ── Ordering: the ceiling is enforced before the bytes are trusted ──────────


def test_an_oversized_file_is_refused_with_413_and_not_with_a_format_error() -> None:
    """A valid PDF header on an 11 MB file must answer 413, not 422.

    The two refusals mean different things to the candidate -- one says "your
    file is too big", the other says "your file is broken" -- and the size
    check must run first or a large valid PDF would be reported as corrupt.
    """
    oversized = PDF_MAGIC + b"x" * MAX_RESUME_BYTES
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(_upload(oversized, "big.pdf", PDF_MIME)))
    assert refused.value.status_code == 413


def test_the_reader_stops_at_the_ceiling_rather_than_buffering_the_whole_body() -> None:
    """A validator that read the whole stream before measuring it would let a
    2 GB upload exhaust the container's memory before the 413 was decided.

    Asserted by handing it a stream far larger than the ceiling and checking
    that the position it left the file at is bounded, which is observable and
    an `await file.read()` would not satisfy.
    """
    body = io.BytesIO(PDF_MAGIC + b"x" * (MAX_RESUME_BYTES * 3))
    upload = UploadFile(
        file=body, filename="huge.pdf", headers=Headers({"content-type": PDF_MIME})
    )
    with pytest.raises(HTTPException) as refused:
        _run(read_validated_resume(upload))
    assert refused.value.status_code == 413
    assert body.tell() <= MAX_RESUME_BYTES + 1, (
        f"the whole body was buffered: read {body.tell()} bytes for a "
        f"{MAX_RESUME_BYTES} byte ceiling"
    )


def _run(coro):
    """Drive one coroutine on its own loop.

    `read_validated_resume` is async only because `UploadFile.read` is. There
    is no application loop involved in any of these, so a fresh one per call
    keeps each test independent of the order it ran in.
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

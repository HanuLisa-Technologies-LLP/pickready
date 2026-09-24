"""The SES event webhook's SNS signature check: every refusal is logged with
its reason, and only a hostile or broken message is answered as a refusal.

WHY THIS FILE EXISTS
--------------------
`_verify_sns_signature` used to wrap the RSA verification in `except
Exception: return False` and log nothing. Two different failures then looked
identical from outside:

  * a forged message, which SHOULD be a quiet 403; and
  * a real SES topic whose every event stopped verifying (a rotated
    certificate, a canonical string built from the wrong field list, a bug),
    which is an outage of delivery tracking that nobody would ever see, because
    the webhook answers 403 and SNS eventually gives up.

The first is still a 403 with nothing revealed to the sender. What changed is
that each refusal writes a WARNING naming its reason, and a programming error
is no longer caught at all, so it surfaces as a 500 that SNS retries.

Everything here runs offline: the certificate is minted in the test and the
HTTP fetch of it goes through `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import datetime as dt
import logging

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import NameOID

from app.api import email_senders as api

CERT_URL = "https://sns.ap-south-1.amazonaws.com/SimpleNotificationService-test.pem"
LOGGER = "app.api.email_senders"


def _certificate(key) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _signed_payload(key, **overrides) -> dict:
    payload = {
        "Type": "Notification",
        "MessageId": "m-1",
        "TopicArn": "arn:aws:sns:ap-south-1:000000000000:ses-events",
        "Message": '{"eventType": "Delivery", "mail": {"messageId": "p-1"}}',
        "Timestamp": "2026-09-24T00:00:00.000Z",
        "SignatureVersion": "2",
        "SigningCertURL": CERT_URL,
    }
    payload.update(overrides)
    signature = key.sign(
        api._sns_canonical_string(payload), padding.PKCS1v15(), hashes.SHA256()
    )
    payload["Signature"] = base64.b64encode(signature).decode("ascii")
    return payload


def _serve(monkeypatch, handler) -> list[str]:
    """Route the verifier's certificate fetch through a MockTransport, and
    record which URLs it asked for."""
    fetched: list[str] = []
    real_client = httpx.AsyncClient

    def recording(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return handler(request)

    class _Client(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return fetched


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.WARNING
    ]


@pytest.mark.asyncio
async def test_a_genuine_message_verifies(monkeypatch, rsa_key, caplog):
    """The healthy path, so every refusal below is a refusal of something and
    not a verifier that answers False to everything."""
    pem = _certificate(rsa_key)
    fetched = _serve(monkeypatch, lambda request: httpx.Response(200, content=pem))
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert await api._verify_sns_signature(_signed_payload(rsa_key)) is True
    assert fetched == [CERT_URL]
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_an_altered_message_is_refused_and_the_refusal_is_logged(
    monkeypatch, rsa_key, caplog
):
    pem = _certificate(rsa_key)
    _serve(monkeypatch, lambda request: httpx.Response(200, content=pem))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    payload = _signed_payload(rsa_key)
    payload["Message"] = '{"eventType": "Bounce", "mail": {"messageId": "p-1"}}'

    assert await api._verify_sns_signature(payload) is False
    assert _warnings(caplog) == [
        "email_senders.sns_signature_invalid reason=InvalidSignature"
    ]


@pytest.mark.asyncio
async def test_a_certificate_whose_key_is_not_rsa_is_refused_and_logged(
    monkeypatch, rsa_key, caplog
):
    """An EC certificate's `verify` takes different arguments. That is a
    hostile or broken message, so it is a refusal, and a logged one."""
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = _certificate(ec_key)
    _serve(monkeypatch, lambda request: httpx.Response(200, content=pem))
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert await api._verify_sns_signature(_signed_payload(rsa_key)) is False
    assert _warnings(caplog) == [
        "email_senders.sns_signature_invalid reason=TypeError"
    ]


@pytest.mark.asyncio
async def test_an_untrusted_certificate_url_is_refused_without_a_fetch(
    monkeypatch, rsa_key, caplog
):
    fetched = _serve(monkeypatch, lambda request: httpx.Response(200))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    payload = _signed_payload(
        rsa_key, SigningCertURL="https://attacker.example.test/cert.pem"
    )

    assert await api._verify_sns_signature(payload) is False
    assert fetched == []
    assert _warnings(caplog) == [
        "email_senders.sns_signature_invalid reason=untrusted_cert_url"
    ]


@pytest.mark.asyncio
async def test_an_undecodable_signature_is_refused_and_logged(
    monkeypatch, rsa_key, caplog
):
    fetched = _serve(monkeypatch, lambda request: httpx.Response(200))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    payload = _signed_payload(rsa_key)
    payload["Signature"] = "not base64 at all!"

    assert await api._verify_sns_signature(payload) is False
    assert fetched == []
    assert _warnings(caplog) == ["email_senders.sns_signature_invalid reason=Error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, reason",
    [
        (lambda request: httpx.Response(503), "HTTPStatusError"),
        (lambda request: httpx.Response(200, content=b"not a certificate"), "ValueError"),
    ],
)
async def test_a_certificate_that_cannot_be_fetched_is_refused_and_logged(
    monkeypatch, rsa_key, caplog, answer, reason
):
    _serve(monkeypatch, answer)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert await api._verify_sns_signature(_signed_payload(rsa_key)) is False
    assert _warnings(caplog) == [f"email_senders.sns_cert_fetch_failed reason={reason}"]


@pytest.mark.asyncio
async def test_a_transport_failure_is_refused_and_logged(monkeypatch, rsa_key, caplog):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    _serve(monkeypatch, unreachable)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    assert await api._verify_sns_signature(_signed_payload(rsa_key)) is False
    assert _warnings(caplog) == [
        "email_senders.sns_cert_fetch_failed reason=ConnectError"
    ]


@pytest.mark.asyncio
async def test_a_programming_error_is_not_answered_as_a_bad_signature(
    monkeypatch, rsa_key
):
    """THE REGRESSION THIS FILE EXISTS FOR. A bug in building the canonical
    string must surface as an error SNS retries, not as a 403 that reads
    exactly like a forged message while every real event is dropped."""
    pem = _certificate(rsa_key)
    _serve(monkeypatch, lambda request: httpx.Response(200, content=pem))
    payload = _signed_payload(rsa_key)

    def broken(_payload: dict) -> bytes:
        raise RuntimeError("canonical string builder is broken")

    monkeypatch.setattr(api, "_sns_canonical_string", broken)
    with pytest.raises(RuntimeError, match="canonical string builder"):
        await api._verify_sns_signature(payload)

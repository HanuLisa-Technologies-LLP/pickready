"""readypick-inbound-email: turn one received message into one webhook call.

WHY A SECOND ZIP FUNCTION, WHEN THE OTHERS SHARE THE BACKEND IMAGE
-------------------------------------------------------------------
Same argument the ECS trigger makes. This function sits on the open internet's
side of the product: anything that can send mail to the reply domain reaches it,
including a spammer, a bounce loop and a deliberately malformed MIME message. It
therefore imports NOTHING but the standard library and boto3, which the runtime
already provides, so the surface a stranger can reach is one file a reviewer can
read in full. Giving it the backend image would put the model router, the prompt
registry and a database session behind an address printed in every verification
email.

It also means this function holds no database credential and no model key. The
one thing it can do is POST to a webhook that authorises itself on a token the
message already carried.

WHAT ARRIVES
------------
SES stores the raw message in S3 and publishes to SNS, so the event is an SNS
envelope carrying SES's own JSON, with the bucket and key under
`receipt.action`.

S3 AND NOT A DIRECT LAMBDA ACTION, deliberately: SES can invoke a function
directly, but that path caps the message at 256KB and changes shape above it. A
verification reply with a scanned letter attached is routinely larger, and "it
works until somebody attaches something" is the kind of limit that is discovered
by the one reply that mattered.

WHAT IS FORWARDED, AND WHAT IS NOT
------------------------------------
The addresses, the subject, the plain-text body and the sending system's
Message-ID. The Message-ID is the load-bearing one: SNS delivers AT LEAST ONCE,
so a redelivery is the default behaviour unless something prevents it, and the
webhook refuses a duplicate on exactly that value.

Attachments are NOT forwarded. They stay in the S3 object, which the deployment
expires on a schedule. Forwarding them would mean this function deciding what a
safe attachment is, on the one path a stranger can reach without authenticating,
and that decision already exists in the product behind a session and a
capability.

FAILURES RAISE
--------------
A message that could not be fetched, parsed or delivered raises, so the
function's error metric moves and the alarm fires. Swallowing it would leave the
platform believing every reply arrived, which is the exact failure this whole
path exists to prevent: a recruiter waiting for an answer that was thrown away.
"""
from __future__ import annotations

import email
import json
import os
import urllib.error
import urllib.request
from email import policy
from email.utils import getaddresses

import boto3
from botocore.config import Config

#: The product's own endpoint. Required rather than defaulted: a function with
#: no destination would accept every message and deliver none, which reads from
#: the outside as an inbox nobody wrote to.
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

#: Matches `MAX_BODY_CHARS` in app/models/conversation.py. Truncated HERE as
#: well as there, so an enormous message is not carried across the network only
#: to be refused on arrival.
MAX_BODY_CHARS = 20_000

WEBHOOK_TIMEOUT_SECONDS = 15

#: Proves to the API that this relay made the call.
#:
#: The webhook writes into verification requests, BGV threads and conversations,
#: and before this it was reachable by anyone on the internet who knew a thread
#: token. Tokens travel by email, so they exist in every mailbox that ever
#: received or forwarded one of these threads.
#:
#: OPTIONAL HERE ON PURPOSE. An environment that has not been given the secret
#: yet sends no header, and the API logs that it is running unauthenticated
#: rather than refusing every genuine reply. Once both sides have the value the
#: API refuses anything without it.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

#: Bounded like every other boto3 client in this tree. An endpoint that accepts
#: the connection and then stops answering would otherwise hold the invocation
#: to its timeout with nothing raised for a caller to catch.
_s3 = boto3.client(
    "s3",
    config=Config(
        connect_timeout=5,
        read_timeout=20,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
)


def _addresses(message, header: str) -> list[str]:
    """Every address on one header, as plain addresses.

    `getaddresses` rather than a split on commas: a display name can legally
    contain a comma, and splitting on one turns `"Nair, Meera" <m@acme.test>`
    into two addresses that are both wrong.
    """
    return [addr for _name, addr in getaddresses(message.get_all(header, [])) if addr]


def _plain_text(message) -> str:
    """The message's readable text.

    text/plain is preferred and text/html is the fallback, because an HTML-only
    reply is common and dropping it would lose the answer entirely. There is no
    HTML-to-text conversion beyond handing the markup over: the product stores
    what arrived, and a lossy transform here would rewrite somebody's words on
    the way in.
    """
    if not message.is_multipart():
        return message.get_content()

    html = ""
    for part in message.walk():
        if part.is_multipart() or part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            return part.get_content()
        if content_type == "text/html" and not html:
            html = part.get_content()
    return html


def _fetch(bucket: str, key: str) -> bytes:
    return _s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _post(payload: dict) -> None:
    headers = {"Content-Type": "application/json"}
    if WEBHOOK_SECRET:
        headers["X-ReadyPick-Webhook-Secret"] = WEBHOOK_SECRET
    request = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as answer:
            if answer.status >= 300:
                raise RuntimeError(f"webhook answered {answer.status}")
    except urllib.error.HTTPError as err:
        # The status is worth keeping and the body is not: a body can quote the
        # message, and this function's logs are far more widely readable than
        # the database the message is going to.
        raise RuntimeError(f"webhook answered {err.code}") from err


def _one_record(record: dict) -> str:
    notice = json.loads(record["Sns"]["Message"])
    action = notice.get("receipt", {}).get("action", {})
    bucket, key = action.get("bucketName"), action.get("objectKey")
    if not bucket or not key:
        # A receipt rule that stopped writing to S3 lands here, and that is a
        # configuration fault rather than a bad message: raised, so somebody is
        # told, rather than counted as a message that happened to be empty.
        raise RuntimeError("SES notification carries no S3 object")

    message = email.message_from_bytes(_fetch(bucket, key), policy=policy.default)
    _post(
        {
            "to": _addresses(message, "To"),
            # Delivered-To is read beside Cc because a reply-all, a forward and
            # a mailing list each put the thread address somewhere different,
            # and a handler that only read To would drop exactly the replies a
            # recruiter was copied on.
            "cc": _addresses(message, "Cc") + _addresses(message, "Delivered-To"),
            "from": (_addresses(message, "From") or [""])[0],
            "subject": message.get("Subject", ""),
            "text": (_plain_text(message) or "")[:MAX_BODY_CHARS],
            # SES's own id when the sender set none, so the webhook always has
            # something to refuse a duplicate on.
            "messageId": message.get("Message-ID")
            or notice.get("mail", {}).get("messageId", ""),
        }
    )
    return key


def handler(event, _context=None):
    """One SNS batch. Every record is delivered, or the invocation fails."""
    delivered = [_one_record(record) for record in event.get("Records", [])]
    return {"delivered": len(delivered)}

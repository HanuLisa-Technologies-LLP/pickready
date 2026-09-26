# The `pickready.app` addresses

The product's domain is `readypick.ai` (RBAC section 15; owner instruction on
2026-09-20 that it stays). Two mailboxes on a different domain,
`pickready.app`, are still written into the code. Whether those mailboxes
exist, and who reads them, is an operational fact this repository cannot
establish, so they are left exactly as they are and listed here for the owner
to rule on. Nothing below is a defect to be "fixed" by a search and replace:
changing an address a customer or a mail provider already relies on is a
decision, not a cleanup.

## Where they are

| Address | File | What reads it |
|---|---|---|
| `noreply@pickready.app` | `backend/app/core/config.py` (`smtp_from_email` default) | The From address of every outbound email when the deployment does not set `SMTP_FROM_EMAIL`. Pilot sets it (`var.platform_from_email`, an SES-verified sender), so the default is reached only by a deployment that forgot the variable. This is the higher risk of the two: it is a RUNTIME default. |
| `hello@pickready.app` | `frontend/app/(org)/org/billing/page.tsx` (two `mailto:` links) | The "Enterprise credits" and "Enterprise plan" contact links a customer clicks on the billing page. |

`backend/tests/test_email_delivery.py` uses `noreply@pickready.app` as a
fixture value for the From address; it asserts header handling and is not a
claim that the mailbox exists.

## Where the domain is named only to say it is not the product's

`frontend/app/layout.tsx`, `frontend/app/robots.ts` and `frontend/lib/site.ts`
each carry a comment stating that `pickready.app` (and the misspelt
`picready.com`) are not the product's address. Those are correct as written.

## The owner question

1. Does `hello@pickready.app` receive mail, and is it read? If not, the two
   billing links should point at a `readypick.ai` mailbox that is.
2. Should `smtp_from_email` default to a `readypick.ai` sender, or to nothing
   at all so that a deployment missing `SMTP_FROM_EMAIL` refuses to send rather
   than sending from an address it may not own?

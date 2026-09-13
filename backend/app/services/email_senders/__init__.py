"""Corporate email sender registration, authorization and lifecycle.

Corporate Email System specification (2026-09-05), amended 2026-09-08.

THE MAILBOX OTP WAS WITHDRAWN, AND THE MODULE WAS DELETED RATHER THAN LEFT
UNIMPORTED. It proved a POC could read the mailbox being registered. SES
already refuses to send as any identity the account has not verified, so that
check re-proved on registration what AWS enforces on every single send -- and
it cost the product an OTP surface in a portal whose audit bans OTP copy
everywhere else. A retained-but-unwired verification module is exactly the
thing the next person re-attaches a route to, so it is gone.

Two questions remain, and they are genuinely different:

    lifecycle    Does this COMPANY authorize the address to speak for it? The
                 Super Admin's decision, and the FSM that records it.
    eligibility  Will AWS carry mail from it? Asked of SES, never asserted,
                 and never cached onto the row.
"""
from app.services.email_senders.eligibility import (  # noqa: F401
    SenderEligibility,
    check_sender_eligibility,
)
from app.services.email_senders.lifecycle import (  # noqa: F401
    IllegalSenderTransition,
    SenderDomainBlocked,
    SenderEmailInvalid,
    assert_transition,
    validate_business_email,
)

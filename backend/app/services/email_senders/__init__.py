"""Corporate email sender registration, verification and lifecycle.

Corporate Email System specification (2026-09-05). This package is
deliberately SEPARATE from `services/otp.py`: that module is the retained SMS
login-OTP machinery, this one proves ownership of a corporate MAILBOX. The two
answer different questions with different rules and entangling them would put
a login concept back into a portal the platform audit bans it from.
"""
from app.services.email_senders.lifecycle import (  # noqa: F401
    IllegalSenderTransition,
    SenderDomainBlocked,
    SenderEmailInvalid,
    assert_transition,
    validate_business_email,
)
from app.services.email_senders.verification import (  # noqa: F401
    OTP_LENGTH,
    ResendCooldownActive,
    VerificationUnavailable,
    VerifyOutcome,
    issue_otp,
    verify_otp,
)

from datetime import datetime, timedelta, timezone
import uuid

import jwt
import pytest

from app.services.resume_access import issue_resume_token, verify_resume_token


def test_resume_token_is_profile_and_tenant_scoped() -> None:
    profile_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = issue_resume_token(profile_id, tenant_id)
    verify_resume_token(token, profile_id, tenant_id)
    with pytest.raises(jwt.InvalidTokenError):
        verify_resume_token(token, profile_id, uuid.uuid4())
    with pytest.raises(jwt.InvalidTokenError):
        verify_resume_token(token, uuid.uuid4(), tenant_id)


def test_resume_token_expires() -> None:
    profile_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = issue_resume_token(
        profile_id, tenant_id, now=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_resume_token(token, profile_id, tenant_id)

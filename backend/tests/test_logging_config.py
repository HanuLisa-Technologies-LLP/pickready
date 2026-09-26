

def test_configure_logging_does_not_construct_settings(monkeypatch) -> None:
    """The 2026-09-19 Lambda outage, pinned.

    The entry points configure logging BEFORE the secret bootstrap loads the
    function's secrets, so building a full validated Settings here trips the
    production JWT refusal on a process whose secrets have not arrived yet,
    and every background task dies at import. The format decision reads the
    ENVIRONMENT variable alone; this asserts both the behaviour (production
    env, no JWT, no exception) and the structure (no get_settings call in
    the source).
    """
    import inspect

    from app.core import logging as core_logging

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    # Must not raise, and must pick the production renderer.
    core_logging.configure_logging()

    source = inspect.getsource(core_logging.configure_logging)
    assert "get_settings()" not in source, (
        "configure_logging constructs Settings again; on Lambda that runs "
        "before the secret bootstrap and dies under the production JWT guard"
    )

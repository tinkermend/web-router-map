from src.services.auth_service import analyze_auth_payload


def test_analyze_auth_prefers_request_header_and_hybrid_mode():
    analysis = analyze_auth_payload(
        request_headers={"authorization": "Bearer token-1"},
        local_storage={"token": "token-2"},
        session_storage={},
        cookies=[{"name": "sessionid", "value": "sid-1"}],
        default_playback_strategy="auto",
    )

    assert analysis.auth_mode == "hybrid"
    assert analysis.playback_strategy == "hybrid"
    assert analysis.authorization_source == "request_header"
    assert analysis.authorization_schema == "Bearer"
    assert analysis.authorization_value == "Bearer token-1"
    assert analysis.auth_fingerprint is not None


def test_analyze_auth_cookie_only():
    analysis = analyze_auth_payload(
        request_headers={},
        local_storage={},
        session_storage={},
        cookies=[{"name": "JSESSIONID", "value": "cookie-token"}],
        default_playback_strategy="auto",
    )

    assert analysis.auth_mode == "cookie_session"
    assert analysis.playback_strategy == "cookie"
    assert analysis.authorization_source == "cookie"
    assert analysis.authorization_value == "cookie-token"


def test_analyze_auth_respects_default_playback_strategy():
    analysis = analyze_auth_payload(
        request_headers={"authorization": "Bearer token-1"},
        local_storage={},
        session_storage={},
        cookies=[],
        default_playback_strategy="header",
    )

    assert analysis.auth_mode == "bearer"
    assert analysis.playback_strategy == "header"

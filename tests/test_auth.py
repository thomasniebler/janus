"""Tests for authentication and security services."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from janus import config, database, models, security
from janus.main import app
from janus.security import security_service
from janus.services import auth_service

# Password hashing and verification at the real bcrypt cost (tests/conftest.py, JAN-311).
pytestmark = pytest.mark.production_password_cost


def test_password_hashing():
    """Tests password hashing and verification."""
    password = "testpassword"
    hashed = security_service.get_password_hash(password)
    assert hashed != password
    assert security_service.verify_password(password, hashed) is True
    assert not security_service.verify_password("wrongpassword", hashed)


# Hashes produced by the previous stack (passlib 1.7.4 + bcrypt 3.2.2), which silently truncated input at 72 bytes.
_LEGACY_HASH_SHORT = "$2b$12$8.7uHiVIk6Y6UCouJXx8rOGOPusqVEKi2l62TWbr0bs/taSr5.Zt2"  # "legacy-secret"
_LEGACY_HASH_80_ASCII = "$2b$12$fQ1S0UK5Yf1fSXSgVJSj7OKud75NG3Uzdv.9O2HaK49WCfdIOJFfW"  # "x" * 80
_LEGACY_HASH_80_UTF8 = "$2b$12$zW4qbzWbRaZS7O8sVxlCeOwRY5aXIBWoeNsZ6VUs77t7YSrqfnyrW"  # "\u00e9" * 40


def test_password_hash_uses_2b_format():
    """New hashes stay in the same $2b$ format as existing stored hashes."""
    assert security_service.get_password_hash("testpassword").startswith("$2b$12$")


def test_legacy_passlib_hashes_still_verify():
    """Hashes stored before dropping passlib must keep verifying, including over-long passwords."""
    assert security_service.verify_password("legacy-secret", _LEGACY_HASH_SHORT) is True
    assert security_service.verify_password("legacy-secreT", _LEGACY_HASH_SHORT) is False
    assert security_service.verify_password("x" * 80, _LEGACY_HASH_80_ASCII) is True
    assert security_service.verify_password("\u00e9" * 40, _LEGACY_HASH_80_UTF8) is True


def test_password_hash_rejects_passwords_over_72_bytes():
    """New passwords over bcrypt's 72-byte limit are refused instead of silently truncated."""
    assert security_service.verify_password("x" * 72, security_service.get_password_hash("x" * 72)) is True
    with pytest.raises(ValueError, match="72 bytes"):
        security_service.get_password_hash("x" * 73)
    # The limit is in UTF-8 bytes, not characters: 37 two-byte characters are 74 bytes.
    with pytest.raises(ValueError, match="72 bytes"):
        security_service.get_password_hash("\u00e9" * 37)


def test_verify_password_rejects_malformed_hash():
    """A corrupt stored hash fails closed instead of raising."""
    assert security_service.verify_password("password", "not-a-bcrypt-hash") is False


def test_create_user_rejects_password_over_72_bytes(client, db):
    """The API refuses over-long new passwords with a validation error before hashing."""
    db.add(models.User(username="admin", hashed_password=security_service.get_password_hash("admin"), is_admin=True))
    db.commit()
    admin_token = security_service.create_access_token({"sub": "admin", "sg": 0})

    response = client.post(
        "/api/auth/users",
        json={"username": "member", "password": "x" * 73},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 422
    assert "72 bytes" in response.text
    assert db.query(models.User).filter(models.User.username == "member").first() is None


def test_jwt_token_flow():
    """Tests JWT token creation and decoding."""
    data = {"sub": "testuser"}
    token = security_service.create_access_token(data)
    assert token is not None

    decoded = security_service.decode_token(token)
    assert decoded["sub"] == "testuser"


def test_token_expiration():
    """Tests JWT token expiration."""
    data = {"sub": "testuser"}
    # Token that expires in the past
    token = security_service.create_access_token(data, expires_delta=timedelta(seconds=-1))
    decoded = security_service.decode_token(token)
    assert decoded is None


def test_bootstrap_admin_cli_creates_first_admin(db, monkeypatch, testing_session_local):
    """The SSH/CLI bootstrap command should create the first admin without the API setup route."""
    import janus.main as main_module

    monkeypatch.setattr(main_module.database, "ensure_schema_up_to_date", lambda database_url=None: None)
    monkeypatch.setattr(main_module.database, "SessionLocal", testing_session_local)

    runner = CliRunner()
    result = runner.invoke(app, ["user", "bootstrap-admin", "owner", "--password", "secret-pass"])

    assert result.exit_code == 0, result.output
    assert "Bootstrapped admin user 'owner'" in result.output

    db = testing_session_local()
    try:
        user = db.query(models.User).filter(models.User.username == "owner").one()
        assert user.is_admin is True
        assert security_service.verify_password("secret-pass", user.hashed_password) is True
    finally:
        db.close()


def test_login_ignores_forwarded_https_without_trusted_proxy(client, db):
    """Spoofed forwarded headers should not mark the session cookie Secure by default."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    response = client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "password"},
        headers={"x-forwarded-proto": "https"},
    )

    assert response.status_code == 200
    assert "Secure" not in response.headers["set-cookie"]
    assert response.json()["csrf_token"]


def test_login_sets_secure_cookie_from_trusted_forwarded_https(client, db, monkeypatch):
    """Trusted proxy headers should make Janus emit a Secure session cookie."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 1)

    response = client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "password"},
        headers={"x-forwarded-proto": "https"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert response.json()["csrf_token"]


def test_login_ignores_short_forwarded_chain_when_more_hops_are_required(client, db, monkeypatch):
    """A shorter-than-configured forwarded chain must not mark the cookie Secure."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 2)

    response = client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "password"},
        headers={"x-forwarded-proto": "https"},
    )

    assert response.status_code == 200
    assert "Secure" not in response.headers["set-cookie"]


def test_csrf_endpoint_returns_session_token(client, db):
    """Authenticated dashboard sessions can fetch their bound CSRF token."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    login_response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    csrf_token = login_response.json()["csrf_token"]

    response = client.get("/api/auth/csrf")

    assert response.status_code == 200
    assert response.json() == {"csrf_token": csrf_token}


def test_csrf_endpoint_rejects_bearer_authenticated_requests(client, db):
    """The CSRF endpoint is only meaningful for cookie-authenticated dashboard sessions."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    token = security_service.create_access_token({"sub": "testuser"})
    response = client.get("/api/auth/csrf", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 400
    assert response.json() == {"detail": "CSRF tokens are only available for cookie-authenticated sessions."}


def test_logout_remains_available_to_legacy_cookie_sessions(client, db):
    """Logout should still work for older cookie sessions minted before the CSRF claim existed."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    legacy_token = security_service.create_access_token({"sub": "testuser"})
    client.cookies.set("access_token", legacy_token)

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "success"}


def test_failed_login_attempts_are_rate_limited_and_audited(client, db, monkeypatch):
    """Repeated failed logins should be throttled and logged with audit metadata."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    monkeypatch.setattr(auth_service.auth_failure_rate_limiter, "max_attempts", 2)

    first = client.post("/api/auth/login", json={"username": "testuser", "password": "wrong"})
    second = client.post("/api/auth/login", json={"username": "testuser", "password": "wrong"})
    third = client.post("/api/auth/login", json={"username": "testuser", "password": "wrong"})

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.headers["Retry-After"]

    events = db.query(models.Event).filter(models.Event.source == "Auth").order_by(models.Event.id).all()
    assert [event.message for event in events] == [
        "Login attempt failed: invalid_credentials.",
        "Login attempt failed: invalid_credentials.",
        "Login attempt throttled.",
    ]
    assert json.loads(events[0].details)["username"] == "testuser"
    assert json.loads(events[2].details)["outcome"] == "throttled"


def test_failed_setup_attempts_are_rate_limited_and_audited(client, db, monkeypatch):
    """Repeated failed public setup attempts should be throttled and logged."""
    monkeypatch.setattr(config.settings, "public_setup_enabled", True)
    monkeypatch.setattr(auth_service.auth_failure_rate_limiter, "max_attempts", 2)

    first = client.post("/api/auth/setup", json={"token": "wrong", "username": "owner", "password": "secret-pass"})
    second = client.post("/api/auth/setup", json={"token": "wrong", "username": "owner", "password": "secret-pass"})
    third = client.post("/api/auth/setup", json={"token": "wrong", "username": "owner", "password": "secret-pass"})

    assert first.status_code == 400
    assert second.status_code == 400
    assert third.status_code == 429

    events = db.query(models.Event).filter(models.Event.source == "Auth").order_by(models.Event.id).all()
    assert [event.message for event in events] == [
        "Setup attempt failed: Setup token is invalid or has expired.",
        "Setup attempt failed: Setup token is invalid or has expired.",
        "Setup attempt throttled.",
    ]
    assert json.loads(events[0].details)["username"] == "owner"


def test_failed_attempt_rate_limiter_caps_bucket_growth():
    """The failed-attempt limiter should evict old buckets when the cap is reached."""
    limiter = auth_service.FailedAttemptRateLimiter(max_attempts=2, max_buckets=2)

    limiter.record_failure("login:ip", "198.51.100.10")
    limiter.record_failure("login:ip", "198.51.100.11")
    limiter.record_failure("login:ip", "198.51.100.12")

    assert len(limiter._failures) == 2
    assert "login:ip:198.51.100.10" not in limiter._failures


def test_login_rate_limit_uses_trusted_forwarded_client_ip(client, db, monkeypatch):
    """Trusted proxy deployments should rate-limit by the forwarded client IP, not the proxy peer."""
    monkeypatch.setattr(config.settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(auth_service.auth_failure_rate_limiter, "max_attempts", 1)

    first = client.post(
        "/api/auth/login",
        json={"username": "user-one", "password": "wrong"},
        headers={"x-forwarded-for": "198.51.100.10"},
    )
    second = client.post(
        "/api/auth/login",
        json={"username": "user-two", "password": "wrong"},
        headers={"x-forwarded-for": "198.51.100.11"},
    )

    assert first.status_code == 401
    assert second.status_code == 401


def test_change_password_failed_attempts_are_rate_limited_and_audited(client, db, monkeypatch):
    """Repeated wrong current-password attempts should be throttled and logged."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()
    monkeypatch.setattr(auth_service.auth_failure_rate_limiter, "max_attempts", 1)

    login_response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    csrf_token = login_response.json()["csrf_token"]

    first = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-secret"},
        headers={"X-CSRF-Token": csrf_token},
    )
    second = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-secret"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert first.status_code == 400
    assert second.status_code == 429

    events = db.query(models.Event).filter(models.Event.source == "Auth").order_by(models.Event.id).all()
    assert [event.message for event in events] == [
        "Password change attempt failed: Current password is incorrect.",
        "Password change attempt throttled.",
    ]


def test_logout_revokes_current_session_before_token_expiry(client, db):
    """Logout should invalidate the current session server-side, not just delete the cookie."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    login_response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    token = client.cookies.get("access_token")

    assert login_response.status_code == 200
    assert token

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_login_prunes_expired_session_revocations(client, db):
    """Successful auth flows should opportunistically delete expired revoked-session rows."""
    hashed_pw = security_service.get_password_hash("password")
    user = models.User(username="testuser", hashed_password=hashed_pw, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(
        models.RevokedSession(
            session_id="expired-session",
            user_id=user.id,
            reason="logout",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    db.commit()

    response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})

    assert response.status_code == 200
    assert db.query(models.RevokedSession).count() == 0


def test_change_password_revokes_existing_sessions(client, db):
    """Changing a password should invalidate already-issued sessions."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    login_response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    token = client.cookies.get("access_token")

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "password", "new_password": "new-secret"},
        headers={"X-CSRF-Token": login_response.json()["csrf_token"]},
    )

    assert response.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "testuser", "password": "new-secret"}).status_code == 200


def test_revoke_token_is_idempotent_for_the_same_session(db):
    """Revoking the same session twice should not fail or duplicate rows."""
    hashed_pw = security_service.get_password_hash("password")
    user = models.User(username="testuser", hashed_password=hashed_pw, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = security_service.create_access_token({"sub": "testuser", "sid": "session-1", "sg": user.session_generation})
    service = auth_service.AuthService(db)

    service.revoke_token(token, reason="logout")
    service.revoke_token(token, reason="logout")

    revoked_sessions = db.query(models.RevokedSession).all()
    assert len(revoked_sessions) == 1
    assert revoked_sessions[0].session_id == "session-1"


def test_admin_session_revocation_invalidates_existing_sessions(client, db):
    """An admin should be able to revoke a user's active sessions explicitly."""
    admin_pw = security_service.get_password_hash("admin-pass")
    user_pw = security_service.get_password_hash("user-pass")
    db.add(models.User(username="admin", hashed_password=admin_pw, is_admin=True))
    db.add(models.User(username="member", hashed_password=user_pw, is_admin=False))
    db.commit()

    user_login = client.post("/api/auth/login", json={"username": "member", "password": "user-pass"})
    user_token = client.cookies.get("access_token")
    admin_token = security_service.create_access_token({"sub": "admin", "sg": 0})

    assert user_login.status_code == 200
    client.cookies.clear()
    revoke_response = client.post(
        "/api/auth/users/member/revoke-sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert revoke_response.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"}).status_code == 401


def _ui_preference_client(client, db):
    """Creates a user and returns a cookie-authenticated, CSRF-armed client."""
    hashed_pw = security_service.get_password_hash("password")
    db.add(models.User(username="testuser", hashed_password=hashed_pw, is_admin=False))
    db.commit()

    login_response = client.post("/api/auth/login", json={"username": "testuser", "password": "password"})
    client.headers[security.CSRF_HEADER_NAME] = login_response.json()["csrf_token"]
    return client


def test_ui_preference_defaults_to_v1(client, db):
    """New accounts start on the classic UI shell."""
    auth_client = _ui_preference_client(client, db)

    response = auth_client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["ui_version"] == "v1"


def test_ui_preference_round_trips_and_persists(client, db):
    """Switching to UIv2 shows up in the PATCH response, later reads, and the database."""
    auth_client = _ui_preference_client(client, db)

    patch_response = auth_client.patch("/api/auth/me/ui-preference", json={"ui_version": "v2"})

    assert patch_response.status_code == 200
    assert patch_response.json()["ui_version"] == "v2"
    assert auth_client.get("/api/auth/me").json()["ui_version"] == "v2"

    db.expire_all()
    assert db.query(models.User).filter(models.User.username == "testuser").one().ui_version == "v2"


def test_ui_preference_round_trips_v3(client, db):
    """UIv3 ("rusty console") round-trips through the API and the database like UIv2 does."""
    auth_client = _ui_preference_client(client, db)

    patch_response = auth_client.patch("/api/auth/me/ui-preference", json={"ui_version": "v3"})

    assert patch_response.status_code == 200
    assert patch_response.json()["ui_version"] == "v3"
    assert auth_client.get("/api/auth/me").json()["ui_version"] == "v3"

    db.expire_all()
    assert db.query(models.User).filter(models.User.username == "testuser").one().ui_version == "v3"


def test_ui_preference_can_switch_back_to_v1(client, db):
    """Users can always return to the classic UI shell."""
    auth_client = _ui_preference_client(client, db)

    assert auth_client.patch("/api/auth/me/ui-preference", json={"ui_version": "v3"}).status_code == 200
    back_response = auth_client.patch("/api/auth/me/ui-preference", json={"ui_version": "v1"})

    assert back_response.status_code == 200
    assert back_response.json()["ui_version"] == "v1"
    assert auth_client.get("/api/auth/me").json()["ui_version"] == "v1"


def test_ui_preference_rejects_unknown_values(client, db):
    """Unknown shell identifiers are rejected and leave the stored preference untouched."""
    auth_client = _ui_preference_client(client, db)

    response = auth_client.patch("/api/auth/me/ui-preference", json={"ui_version": "v9"})

    assert response.status_code == 422
    assert auth_client.get("/api/auth/me").json()["ui_version"] == "v1"


def test_ui_preference_requires_authentication(client, db):
    """Anonymous callers cannot change a UI shell preference."""
    response = client.patch("/api/auth/me/ui-preference", json={"ui_version": "v2"})

    assert response.status_code == 401


def test_revoked_session_prune_releases_the_sqlite_write_lock(tmp_path, monkeypatch):
    """A prune that deletes nothing must not leave its write transaction open (JAN-218).

    The no-op DELETE takes SQLite's write lock; leaving it uncommitted made the next
    write from another connection (the auth audit event of a failed setup or login)
    wait out the busy timeout and then fail with "database is locked".
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'prune.db'}", connect_args={"timeout": 0.2})
    database.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(auth_service, "_last_revoked_session_prune_at", None)

    request_db = session_factory()
    other_db = session_factory()
    try:
        auth_service.maybe_prune_expired_revoked_sessions(request_db)

        other_db.add(models.Event(event_type=models.EventType.WARNING, source="Auth", message="audit"))
        other_db.commit()

        assert other_db.query(models.Event).count() == 1
    finally:
        request_db.close()
        other_db.close()
        engine.dispose()

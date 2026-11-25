"""Authentication API router for Janus."""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from janus import config, database, models, security
from janus.request_origin import is_secure_request, public_base_url, request_client_ip
from janus.schemas import auth as auth_schemas
from janus.services import (
    auth_service,
    character_state_service,
    google_client,
    google_connect_service,
    google_oauth,
    telegram_service,
    webhook_service,
)
from janus.services import oauth_connection_service as connection_service
from janus.services.a2a_service import A2AService
from janus.services.device_token_service import DeviceTokenService

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    return request_client_ip(request)


def _enforce_failed_attempt_rate_limit(*, action: str, request: Request, username: str | None) -> None:
    client_ip = _client_ip(request)
    rate_limit = auth_service.check_failed_attempt_rate_limit(action, client_ip, username)
    if not rate_limit.limited:
        return

    auth_service.log_auth_throttle(action, client_ip, username, rate_limit.retry_after_seconds)
    headers = {}
    if rate_limit.retry_after_seconds is not None:
        headers["Retry-After"] = str(rate_limit.retry_after_seconds)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed attempts.",
        headers=headers,
    )


def _require_public_setup_enabled() -> None:
    """Raises 404 when the public setup endpoints are disabled."""
    if not config.settings.public_setup_enabled:
        raise HTTPException(status_code=404, detail="Initial setup is not available.")


def _issue_session_cookie(response: Response, request: Request, user: models.User) -> tuple[str, str]:
    """Create a JWT access token for `user`, set it as the session cookie, and return (token, csrf_token).

    Shared by every login path (password login, Google callback) so cookie
    flags and JWT claim shape can't drift between them.
    """
    csrf_token = security.create_csrf_token()
    access_token = security.security_service.create_access_token(
        data={
            "sub": user.username,
            "csrf": csrf_token,
            "sid": security.create_session_id(),
            "sg": user.session_generation,
        }
    )
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
        secure=is_secure_request(request),
    )
    return access_token, csrf_token


@router.post("/login")
async def login(
    login_data: auth_schemas.LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(database.get_db),
):
    """Authenticates a user and sets a secure JWT cookie."""
    client_ip = _client_ip(request)
    _enforce_failed_attempt_rate_limit(action="login", request=request, username=login_data.username)

    service = auth_service.AuthService(db)
    user = service.authenticate_user(login_data.username, login_data.password)

    if not user:
        auth_service.record_failed_attempt("login", client_ip, login_data.username, "invalid_credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service.reset_failed_attempts("login", client_ip, login_data.username)
    access_token, csrf_token = _issue_session_cookie(response, request, user)

    return {
        "status": "success",
        "access_token": access_token,
        "token_type": "bearer",
        "csrf_token": csrf_token,
    }


@router.get("/setup", response_model=auth_schemas.SetupStatus)
async def get_setup_status(db: Session = Depends(database.get_db)):
    """Returns whether Janus still needs its initial admin account."""
    _require_public_setup_enabled()
    return {"needs_setup": auth_service.needs_initial_setup(db)}


@router.post("/setup", response_model=auth_schemas.User)
async def complete_initial_setup(
    setup_data: auth_schemas.InitialSetupRequest,
    request: Request,
    db: Session = Depends(database.get_db),
):
    """Consumes a one-time token to create the first admin account."""
    _require_public_setup_enabled()
    client_ip = _client_ip(request)
    _enforce_failed_attempt_rate_limit(action="setup", request=request, username=setup_data.username)
    try:
        user = auth_service.create_initial_admin(db, setup_data.token, setup_data.username, setup_data.password)
        auth_service.reset_failed_attempts("setup", client_ip, setup_data.username)
        return user
    except ValueError as exc:
        message = str(exc)
        auth_service.record_failed_attempt("setup", client_ip, setup_data.username, message)
        if message == "Initial setup has already been completed.":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(database.get_db),
    token: security.AuthToken | None = Depends(security.get_token),
):
    """Clears the authentication cookie."""
    if token is not None:
        auth_service.AuthService(db).revoke_token(token.token, reason="logout")
    response.delete_cookie("access_token", secure=is_secure_request(request), httponly=True, samesite="lax")
    return {"status": "success"}


@router.post("/change-password")
async def change_password(
    payload: auth_schemas.PasswordChangeRequest,
    request: Request,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Changes the current user's password and invalidates existing sessions."""
    client_ip = _client_ip(request)
    _enforce_failed_attempt_rate_limit(action="password change", request=request, username=current_user.username)
    try:
        auth_service.AuthService(db).change_password(current_user, payload.current_password, payload.new_password)
    except ValueError as exc:
        auth_service.record_failed_attempt("password change", client_ip, current_user.username, str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    auth_service.reset_failed_attempts("password change", client_ip, current_user.username)
    return {"status": "success"}


@router.get("/csrf", response_model=auth_schemas.SessionCsrfToken)
async def get_session_csrf_token(
    current_user: models.User = Depends(security.get_current_user),
    token: security.AuthToken | None = Depends(security.get_token),
):
    """Returns the CSRF token bound to the current authenticated session."""
    _ = current_user
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if token.source != "cookie":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSRF tokens are only available for cookie-authenticated sessions.",
        )

    payload = security.security_service.decode_token(token.token)
    csrf_token = payload.get("csrf") if payload else None
    if not isinstance(csrf_token, str) or not csrf_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session does not expose a CSRF token")
    return {"csrf_token": csrf_token}


@router.get("/me", response_model=auth_schemas.User)
async def get_me(current_user: models.User = Depends(security.get_current_user)):
    """Returns the currently authenticated user."""
    return current_user


@router.patch("/me/ui-preference", response_model=auth_schemas.User)
async def update_ui_preference(
    payload: auth_schemas.UIPreferenceUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Switches the current user's app shell between the classic UI (v1), UIv2 and UIv3."""
    service = auth_service.AuthService(db)
    return service.update_ui_preference(current_user, payload.ui_version)


@router.post("/users", response_model=auth_schemas.User)
async def create_user(
    user_data: auth_schemas.UserCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.admin_required),
):
    """Creates a new system user."""
    service = auth_service.AuthService(db)
    if service.get_user_by_username(user_data.username):
        raise HTTPException(status_code=400, detail="User already exists")
    return service.create_user(user_data.username, user_data.password)


@router.get("/users", response_model=list[auth_schemas.User])
async def list_users(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.admin_required),
):
    """Lists all system users."""
    return db.query(models.User).all()


@router.delete("/users/{username}")
async def delete_user(
    username: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.admin_required),
):
    """Deletes a system user."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Their event-log rows become system rows (admin-only) rather than
    # dangling ids a later user could inherit: SQLite enforces no FK (JAN-272).
    db.query(models.Event).filter(models.Event.user_id == user.id).update({models.Event.user_id: None})
    # Adaptive character state, proposals and audit rows go with the user (JAN-301).
    character_state_service.delete_for_user(db, user.id)
    # Device tokens go with the user: SQLite may reuse the id, and a leftover row would authenticate as the next user.
    DeviceTokenService(db).delete_all(user.id)
    # Webhook secrets and remote-agent tokens too, for the same reason (JAN-406, JAN-404).
    webhook_service.delete_all_for_user(db, user.id)
    A2AService(db).delete_all_for_user(user.id)
    telegram_service.delete_all_for_user(db, user.id)
    db.delete(user)
    db.commit()
    return {"status": "success"}


@router.post("/users/{username}/revoke-sessions")
async def revoke_user_sessions(
    username: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.admin_required),
):
    """Allows an admin to invalidate another user's active sessions."""
    service = auth_service.AuthService(db)
    user = service.get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    service.revoke_user_sessions(user, reason="admin_revocation", actor=current_user.username)
    return {"status": "success"}


def _build_google_login_flow(request: Request, state: str | None = None, code_verifier: str | None = None) -> Flow:
    """Builds a Google OAuth2 flow scoped to login (openid + email) from the configured client.

    When ``code_verifier`` is supplied (reconstructing the flow for the callback), PKCE
    auto-generation is disabled so the flow uses the exact verifier of the original request.
    """
    try:
        return google_client.build_flow(
            google_client.LOGIN_SCOPES,
            f"{public_base_url(request)}{google_connect_service.CALLBACK_PATH}",
            state,
            code_verifier,
        )
    except google_client.GoogleNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth2 credentials are not configured.",
        ) from exc


@router.get("/google")
async def google_login(request: Request, db: Session = Depends(database.get_db)):
    """Initiates Google OAuth2 login. Redirects to Google's consent screen."""
    state_token = secrets.token_urlsafe(32)
    oauth_state = models.OAuthState(state=state_token, user_id=None)
    db.add(oauth_state)
    db.commit()

    try:
        flow = _build_google_login_flow(request)
    except HTTPException:
        db.delete(oauth_state)
        db.commit()
        return RedirectResponse(url="/login?error=google_not_configured")
    authorization_url, _ = flow.authorization_url(
        access_type="online",
        state=state_token,
        include_granted_scopes="false",
        prompt="select_account",
    )
    # PKCE: the flow auto-generated a code_verifier while building the
    # authorization URL above. Persist it so the callback (which must build a
    # separate Flow instance) can complete the exchange with the matching
    # code_verifier instead of silently minting its own.
    oauth_state.code_verifier = flow.code_verifier
    db.commit()
    return RedirectResponse(url=authorization_url)


def finish_google_login(
    request: Request,
    db: Session,
    flow_state: connection_service.FlowState,
    query: dict[str, str],
    redirect_uri: str,
) -> RedirectResponse:
    """Completes a Google Sign-In from the shared ``/api/connections/google/callback``.

    The caller has checked and consumed the single-use state. Issues a JWT cookie for a known
    user only; every failure goes back to the login page with an ``error`` code.
    """
    if query.get("error"):
        return RedirectResponse(url="/login?error=access_denied")
    code = query.get("code")
    if not code:
        return RedirectResponse(url="/login?error=missing_params")
    try:
        flow = google_client.build_flow(
            google_client.LOGIN_SCOPES, redirect_uri, query.get("state"), flow_state.code_verifier
        )
        # The state was checked by the caller; exchange the bare code (JAN-318, see google_oauth.exchange_code).
        credentials = google_oauth.exchange_code(flow, code)
    except (google_client.GoogleNotConfiguredError, ValueError, google_oauth.GoogleOAuthError) as exc:
        logger.warning("Google login token exchange failed: %s", getattr(exc, "code", type(exc).__name__))
        return RedirectResponse(url="/login?error=token_exchange_failed")

    userinfo = google_client.fetch_userinfo(credentials.token)
    if not userinfo:
        return RedirectResponse(url="/login?error=userinfo_failed")
    google_id = userinfo.get("sub")
    if not google_id:
        return RedirectResponse(url="/login?error=no_google_id")
    user = db.query(models.User).filter(models.User.google_id == google_id).first()
    if not user:
        return RedirectResponse(url="/login?error=google_user_not_found")

    # Set the cookie on the response actually returned: headers on an injected `response` are dropped.
    redirect = RedirectResponse(url="/", status_code=303)
    _issue_session_cookie(redirect, request, user)
    return redirect


@router.post("/users/{username}/link-google")
async def link_google_account(
    username: str,
    body: auth_schemas.LinkGoogleRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.admin_required),
):
    """Links a Google account (by google_id) to an existing user. Admin-only.

    Accepts JSON body: {"google_id": "<google_sub_value>"}
    """
    google_id = body.google_id

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(models.User).filter(models.User.google_id == google_id).first()
    if existing and existing.id != user.id:
        raise HTTPException(status_code=409, detail="This Google account is already linked to another user")

    user.google_id = google_id
    db.commit()
    return {"status": "success", "username": user.username, "google_id": google_id}


@router.delete("/google/link")
async def google_unlink(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Removes the Google account link from the currently logged-in user."""
    if not current_user.google_id:
        raise HTTPException(status_code=400, detail="No Google account is linked to your account.")
    current_user.google_id = None
    db.commit()
    return {"status": "success"}

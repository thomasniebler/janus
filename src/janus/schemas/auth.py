"""Authentication and user schemas for Janus API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from janus.security import validate_password_length

UIVersion = Literal["v1", "v2", "v3"]
# A password being set (not merely checked): bcrypt only covers 72 UTF-8 bytes, so longer ones are rejected.
NewPassword = Annotated[str, AfterValidator(validate_password_length)]


class UserBase(BaseModel):
    """Base user schema."""

    username: str


class UserCreate(UserBase):
    """Schema for creating a new user."""

    password: NewPassword


class PasswordChangeRequest(BaseModel):
    """Schema for changing the current user's password."""

    current_password: str
    new_password: NewPassword


class User(UserBase):
    """Schema for a system user."""

    id: int
    is_admin: bool = False
    last_login: datetime | None = None
    google_linked: bool = False
    ui_version: UIVersion = "v1"

    model_config = ConfigDict(from_attributes=True)


class UIPreferenceUpdate(BaseModel):
    """Schema for switching the current user's app shell between the available UI versions."""

    ui_version: UIVersion


class Token(BaseModel):
    """Schema for a JWT token."""

    access_token: str
    token_type: str


class LoginRequest(BaseModel):
    """Schema for login request."""

    username: str
    password: str


class SetupStatus(BaseModel):
    """Schema for first-run setup status."""

    needs_setup: bool


class SessionCsrfToken(BaseModel):
    """Schema for a CSRF token bound to the current authenticated session."""

    csrf_token: str


class InitialSetupRequest(BaseModel):
    """Schema for creating the initial admin user."""

    token: str
    username: str
    password: NewPassword


class LinkGoogleRequest(BaseModel):
    """Schema for linking a Google account to an existing user."""

    google_id: str

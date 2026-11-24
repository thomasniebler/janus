# Tech Spec: Google OAuth Integration

**Status:** Superseded (historical proposal; shipped, but this document was not kept in sync with the implementation)
**Linear:** not tracked (this feature predates the [Delivery Artifacts](../../tech-spec/delivery-artifacts.md) convention; link the originating issue here if it is recovered)
**Depends on:** None
**Scope:** OAuth 2.0 Authorization Code flow for Google Calendar, Google Tasks, and Gemini LLM backend.

> **This page describes the original design proposal, not the shipped implementation.** The
> feature shipped, but the endpoints, request/response shapes, and scope notes below drifted from
> reality (for example, the actual OAuth routes live under `/api/planner/google/*` in
> `src/janus/api/planner.py`, not `/api/auth/google/*` as described in §5 below). For the current,
> authoritative behavior see [`auth-security.md`](../../tech-spec/auth-security.md) and
> [`backend-api.md`](../../tech-spec/backend-api.md). This page is kept only as a historical record
> of the original design intent; do not implement against it.

---

## 1. Background and Goals

The current Janus backend has a `GoogleCalendarConnector` that depends on a pre-serialized OAuth
credential blob stored manually in `PlannerAccount.token`. There is no in-app flow to acquire or
refresh these credentials, no Google Tasks connector exists, and Gemini is not available as an LLM
provider.

This feature adds:

- An OAuth 2.0 Authorization Code flow (initiation + callback) so users can connect their Google
  account directly from the Janus dashboard.
- Automatic token refresh so credentials never expire silently.
- A **Google Tasks connector** that reads from the Google Tasks API v1.
- **Gemini** as a third LLM provider option (manual API key, identical UX to the existing OpenAI
  key entry).

---

## 2. Scope

### In Scope

| Item | Notes |
|---|---|
| OAuth 2.0 initiation + callback endpoints | Flexible per-scope authorization; no bundled consent |
| CSRF `state` token stored in DB with TTL | 10-minute expiry; cleaned up on use or timeout |
| Token auto-refresh in `GoogleCalendarConnector` | Detects expired `access_token`; uses `refresh_token` transparently |
| `GoogleTasksConnector` | New connector implementing `Connector` ABC; read-only Tasks API v1 |
| Gemini as LLM backend | Manual API key entry; `langchain-google-genai` integration |

### Out of Scope

| Item | Reason |
|---|---|
| Gmail REST API connector | IMAP (App Passwords) already works; Gmail push requires Pub/Sub + public HTTPS |
| Frontend "Connect Google" UI | Depends on stable backend endpoints; planned as a follow-up PR |
| Automatic Gemini key generation via OAuth | Technically infeasible — no public API to create AI Studio keys programmatically |
| Multi-user OAuth token scoping | `PlannerAccount` has no `user_id`; requires schema redesign |
| Removing IMAP support | IMAP serves non-Gmail providers without Google OAuth |

---

## 3. Data Model Changes

### 3.1 New Table: `OAuthState`

A short-lived record used for CSRF protection during the OAuth redirect dance.

```python
class OAuthState(database.Base):
    __tablename__ = "oauth_states"

    id = Column(Integer, primary_key=True)
    state = Column(String, unique=True, index=True)  # random URL-safe token
    scopes = Column(String)  # comma-separated scope list
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)  # created_at + 10 minutes
```

### 3.2 `models.Provider` enum — add `GOOGLE_TASKS`

```python
class Provider(enum.Enum):
    GOOGLE_CALENDAR = "google_calendar"
    GOOGLE_TASKS = "google_tasks"  # NEW
    O365 = "o365"
    JIRA = "jira"
    ADO = "ado"
```

### 3.3 `models.LLMProvider` enum — add `GEMINI`

```python
class LLMProvider(enum.Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    GEMINI = "gemini"  # NEW
```

### 3.4 `models.Settings` — add OAuth client fields and `gemini_api_key`

The `models.Settings` DB table is the single source of truth for all user-configurable runtime
settings (LLM provider, API keys, OAuth client credentials). It is distinct from
`janus.config.Settings`, which is a pydantic-settings class sourced from `.env` and used
exclusively for bootstrap secrets needed before any DB connection is available (JWT signing key,
algorithm, token expiry). No new fields are added to `janus.config.Settings`.

```python
class Settings(database.Base):
    ...
    # OAuth client credentials — entered via the Janus settings UI; google_client_secret
    # is Fernet-encrypted at rest (same pattern as openai_api_key)
    google_client_id = Column(String, nullable=True)
    google_client_secret = Column(String, nullable=True)  # Fernet-encrypted; NEW
    google_redirect_uri = Column(String, nullable=True, default="http://localhost:8080/api/auth/google/callback")
    # Gemini LLM
    gemini_api_key = Column(String, nullable=True)  # Fernet-encrypted; NEW
```

### 3.5 Alembic migrations

Two migrations are required (keep them separate to allow independent rollback):

1. **`add_oauth_state_table`** — creates `oauth_states`.
2. **`add_google_oauth_and_gemini`** — adds `google_client_id`, `google_client_secret`,
   `google_redirect_uri`, and `gemini_api_key` to `settings`; adds `GOOGLE_TASKS` to `Provider`
   and `GEMINI` to `LLMProvider` (SQLite: recreate table; PostgreSQL: `ALTER TYPE`).

---

## 4. Settings Architecture

### 4.1 Single source of truth: `models.Settings` (DB)

Janus has two `Settings` objects; their roles are intentionally distinct:

| Class | Source | Purpose |
|---|---|---|
| `janus.config.Settings` | `.env` file (pydantic-settings) | **Bootstrap secrets only** — JWT signing key, algorithm, token expiry. Needed before DB access is available. No new fields added here. |
| `models.Settings` | SQLAlchemy DB table | **All user-configurable runtime settings** — LLM provider, API keys, OAuth client credentials. Read and written via the Janus settings UI and API. |

All new Google OAuth and Gemini settings are added to `models.Settings` (the DB table), keeping
`.env` minimal and avoiding the dual-`Settings` confusion.

### 4.2 How OAuth client credentials are read

The `/api/auth/google/initiate` and `/api/auth/google/callback` endpoints read credentials from
the DB `settings` row:

```python
db_settings = db.query(models.Settings).first()
client_id = db_settings.google_client_id
client_secret = security.security_service.decrypt(db_settings.google_client_secret)
redirect_uri = db_settings.google_redirect_uri
```

`google_client_secret` is stored Fernet-encrypted at rest, decrypted on demand — identical to
the existing `openai_api_key` pattern.

---

## 5. New API Endpoints

Both endpoints live in `src/janus/api/auth.py` and are automatically included via `auth.router`
(already registered in `main.py`).

### 5.1 `GET /api/auth/google/initiate`

Starts the OAuth flow for the given scopes.

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `scopes` | `str` | No | `"calendar,tasks"` | Comma-separated list: `calendar`, `tasks` |

**Scope → Google OAuth scope mapping:**

| Short name | Google scope URI |
|---|---|
| `calendar` | `https://www.googleapis.com/auth/calendar.readonly` |
| `tasks` | `https://www.googleapis.com/auth/tasks.readonly` |

**Response:** `200 OK`

```json
{ "auth_url": "https://accounts.google.com/o/oauth2/auth?..." }
```

**Errors:**

| Code | Condition |
|---|---|
| `400` | Unknown scope name in `scopes` parameter |
| `503` | `google_client_id` or `google_client_secret` not set in `models.Settings` |

**Internal behavior:**

1. Validate the requested scopes against the allowed list.
2. Generate a 32-byte URL-safe random `state` token via `secrets.token_urlsafe(32)`.
3. Persist an `OAuthState` row (`expires_at = now + 10 min`).
4. Build the authorization URL using `google_auth_oauthlib.flow.Flow`.
5. Return `{"auth_url": url}`.

### 5.2 `GET /api/auth/google/callback`

Handles the Google redirect, exchanges the code for tokens, and stores credentials.

**Query parameters** (set by Google):

| Parameter | Type | Description |
|---|---|---|
| `code` | `str` | Authorization code from Google |
| `state` | `str` | The `state` token issued by `/initiate` |

**Response:** `200 OK`

```json
{ "status": "success", "provider": "google_calendar" }
```

**Errors:**

| Code | Condition |
|---|---|
| `400` | `state` missing or not found in DB |
| `400` | `state` token expired |
| `400` | Code exchange failed (invalid/expired code) |

**Internal behavior:**

1. Look up the `OAuthState` row by `state`; raise `400` if missing or expired.
2. Delete the `OAuthState` row immediately (one-time use).
3. Exchange the `code` for credentials using `google_auth_oauthlib.flow.Flow.fetch_token`.
4. Serialize credentials to JSON (`credentials.to_json()`).
5. Encrypt with `security.security_service.encrypt(creds_json)`.
6. Upsert a `PlannerAccount` row:
   - `name` = `"Google (Calendar & Tasks)"` if both scopes, or scope-specific name.
   - `provider` = `Provider.GOOGLE_CALENDAR` (the primary connector type; Tasks uses the same
     credential).
   - `token` = encrypted credentials JSON.
7. Return `{"status": "success", "provider": account.provider.value}`.

---

## 6. Connector Changes

### 6.1 `GoogleCalendarConnector` — token auto-refresh

Update `fetch_events()` and `test_connection()` to refresh expired credentials:

```python
from google.auth.transport.requests import Request


def _get_credentials(self) -> Credentials:
    creds_data = json.loads(self.decrypted_token)
    creds = Credentials.from_authorized_user_info(creds_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist refreshed token back to DB
        self.account.token = security.security_service.encrypt(creds.to_json())
        # Caller is responsible for db.commit()
    return creds
```

Both `fetch_events()` and `test_connection()` call `_get_credentials()` instead of constructing
`Credentials` directly.

### 6.2 New `GoogleTasksConnector`

**File:** `src/janus/connectors/google.py` (same file as `GoogleCalendarConnector`).

**Required Google OAuth scope:** `https://www.googleapis.com/auth/tasks.readonly`

```python
class GoogleTasksConnector(base.Connector):
    def fetch_tasks(self) -> List[dict]:
        """Fetch tasks from all Google Task Lists."""
        ...

    def fetch_events(self, start_time, end_time) -> List[dict]:
        """Google Tasks has no calendar events."""
        return []

    def test_connection(self) -> tuple[bool, str]:
        """Tests the connection to the Google Tasks API."""
        ...
```

`fetch_tasks()` implementation:

1. Load and (if needed) refresh credentials via `_get_credentials()` (shared helper from
   `GoogleCalendarConnector`).
2. Build `service = build("tasks", "v1", credentials=creds)`.
3. List all task lists: `service.tasklists().list().execute()`.
4. For each task list, list tasks with `showCompleted=False`.
5. Return `[{"external_id": task["id"], "title": task["title"], "status": task["status"],
   "due_date": ...}]`.

### 6.3 `PlannerService` — register `GoogleTasksConnector`

Update `get_connector()` in `planner_service.py`:

```python
elif account.provider == models.Provider.GOOGLE_TASKS:
    return google.GoogleTasksConnector(account)
```

---

## 7. LLM Service Changes

### 7.1 `models.LLMProvider` — `GEMINI` branch in `LLMService`

**New dependency:** `langchain-google-genai` (add to `pyproject.toml`).

In `LLMService.__init__()`, add a `GEMINI` branch following the same pattern as `OPENAI`.
`LLMService` already reads from `models.Settings` (DB), so no change in how settings are accessed:

```python
elif settings.llm_provider == models.LLMProvider.GEMINI:
    api_key = None
    if settings.gemini_api_key:
        api_key = security.security_service.decrypt(settings.gemini_api_key)
    if not api_key:
        raise ValueError("Gemini API key not found in settings.")
    from langchain_google_genai import ChatGoogleGenerativeAI
    self.llm = ChatGoogleGenerativeAI(model=settings.llm_model, google_api_key=api_key)
```

Here `settings` is the `models.Settings` DB row (already queried at the top of `__init__()`), not
`janus.config.Settings`.

The `llm_model` field in `models.Settings` is reused; recommended default when provider is Gemini:
`"gemini-1.5-flash"`.

The API key is stored encrypted in `models.Settings.gemini_api_key`, entered manually by the user
via a new Settings UI field (frontend PR).

---

## 8. Security Considerations

| Concern | Mitigation |
|---|---|
| CSRF during OAuth redirect | `state` token is cryptographically random (`secrets.token_urlsafe(32)`), single-use, and expires after 10 minutes |
| Credential storage | OAuth `access_token` + `refresh_token` serialized to JSON and encrypted at rest with Fernet (`SecurityService`) |
| Client secret exposure | `google_client_secret` stored Fernet-encrypted in `models.Settings`; decrypted only in-process; never returned in API responses or logged |
| Scope creep | Scopes are validated against an explicit allow-list in `/initiate`; unknown scopes return `400` |
| Token leakage in logs | Credentials JSON must never be passed to `logger.*` — only log `account.name` / `provider` |
| Stale `OAuthState` rows | A background cleanup (or Alembic `expires_at` check on callback) removes expired rows |

---

## 9. Dependencies

| Package | Version constraint | Purpose |
|---|---|---|
| `google-auth-oauthlib` | Already in `pyproject.toml` (`>=1.2.0`) | OAuth flow initiation and code exchange |
| `google-api-python-client` | Already in `pyproject.toml` (`>=2.191.0`) | Calendar + Tasks API client |
| `langchain-google-genai` | `>=2.0.0` | Gemini LLM backend for LangChain |

Only `langchain-google-genai` is a new addition.

---

## 10. Non-Goals (Explicit Rejections)

- **Automatic Gemini API key provisioning:** The Google Cloud API (`serviceusage.keys.create`)
  requires IAM roles and Cloud Resource Manager access — not accessible via a standard user OAuth
  flow. Users obtain a key from [Google AI Studio](https://aistudio.google.com) and paste it into
  the Janus settings UI.
- **Gmail REST API connector:** The Gmail API push model (`watch()`) requires a publicly reachable
  HTTPS endpoint with a valid certificate, incompatible with a local installation. The current
  IMAP `username`/`password` path (including App Passwords) remains the supported Gmail integration.
  IMAP XOAUTH2 is not currently implemented and would require additional work.
- **Multi-user OAuth:** `PlannerAccount` has no `user_id` foreign key. Proper per-user token
  isolation requires a schema migration and is deferred.

# Implementation Plan: Google OAuth Integration

**Status:** Shipped
**Linear:** not tracked (this feature predates the [Delivery Artifacts](../../tech-spec/delivery-artifacts.md) convention; link the originating issue here if it is recovered)
**Depends on:** None
**Spec:** [Google OAuth Tech Spec](google-oauth-tech-spec.md)

> This plan shipped across several releases (see `CHANGELOG.md` entries for
> `google`/`oauth` under `auth` and `planner`). It is kept as a historical
> implementation record; the steps below describe how it was built, not
> outstanding work.

This document breaks the Google OAuth integration into small, independently reviewable steps. Each
step produces a shippable commit. Steps 1–6 form a single feature branch; Step 7 (frontend) ships
as a separate PR after the backend is stable.

---

## Prerequisites

Before starting:

- [ ] Create a Google Cloud project.
- [ ] Enable the **Google Calendar API**, **Google Tasks API** in the GCP console.
- [ ] Configure an OAuth Consent Screen (internal scope is sufficient for personal use).
- [ ] Add the redirect URI `http://localhost:8080/api/auth/google/callback` to the allowed list.
- [ ] Download the OAuth client credentials and note `client_id` and `client_secret`.
- [ ] Add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` to your local `.env`.

---

## Step 1 — DB Settings: OAuth Client Fields and Gemini Key

**Files:** `src/janus/models.py`, `alembic/versions/<hash>_add_google_oauth_and_gemini.py`

**Context:** Janus has two `Settings` objects. `janus.config.Settings` (pydantic-settings, `.env`)
holds bootstrap secrets only (JWT key, algorithm, expiry) and **no new fields are added there**.
All user-configurable credentials live in `models.Settings` (DB), which is also where
`openai_api_key` lives today.

**Changes:**

Add four new nullable columns to `models.Settings`:

```python
google_client_id = Column(String, nullable=True)
google_client_secret = Column(String, nullable=True)  # Fernet-encrypted
google_redirect_uri = Column(String, nullable=True, default="http://localhost:8080/api/auth/google/callback")
gemini_api_key = Column(String, nullable=True)  # Fernet-encrypted
```

Also add `GOOGLE_TASKS = "google_tasks"` to `models.Provider` and `GEMINI = "gemini"` to
`models.LLMProvider`.

Generate migration:

```bash
uv run alembic revision --autogenerate -m "add_google_oauth_and_gemini"
```

**Acceptance criteria:**

- [ ] `uv run alembic upgrade head` succeeds on a clean DB.
- [ ] `models.Settings` has the four new columns with `None` as default.
- [ ] `models.Provider.GOOGLE_TASKS` and `models.LLMProvider.GEMINI` are importable.
- [ ] No changes to `janus.config.Settings` (`config.py`) in this step.

**Tests:** Migration correctness verified by upgrade/downgrade round-trip; no new unit tests needed.

---

## Step 2 — `OAuthState` Database Model and Migration

**Files:** `src/janus/models.py`, `alembic/versions/<hash>_add_oauth_state_table.py`

**Changes:**

1. Add `OAuthState` to `models.py` (see tech spec §3.1).
2. Generate migration:

```bash
uv run alembic revision --autogenerate -m "add_oauth_state_table"
```

3. Review the generated migration; ensure only `oauth_states` is created.

**Acceptance criteria:**

- [ ] `uv run alembic upgrade head` succeeds on a clean DB.
- [ ] `oauth_states` table exists with columns: `id`, `state`, `scopes`, `created_at`,
  `expires_at`.
- [ ] `OAuthState` is importable from `janus.models`.

**Tests:** No unit tests; migration correctness verified by upgrade/downgrade round-trip.

---

## Step 3 — OAuth API Endpoints

**Files:** `src/janus/api/auth.py`

**Changes:**

Implement two new endpoints on the existing `auth.router` (already mounted at `/api/auth`):

1. `GET /api/auth/google/initiate` — see tech spec §5.1.
2. `GET /api/auth/google/callback` — see tech spec §5.2.

Key implementation notes:

- Use `secrets.token_urlsafe(32)` for `state` generation.
- Use `google_auth_oauthlib.flow.Flow.from_client_config(...)` with the config dict built from
  `db_settings.google_client_id` / decrypted `db_settings.google_client_secret` (read from the
  DB `models.Settings` row — not from `janus.config.Settings`).
- Delete `OAuthState` row immediately after validation in the callback.
- Upsert `PlannerAccount` using `name="Google"` as the unique key; do not create duplicates.

**Acceptance criteria:**

- [ ] `GET /api/auth/google/initiate` returns `{"auth_url": "https://..."}` when client ID/secret
  are configured.
- [ ] `GET /api/auth/google/initiate` returns `503` when `google_client_id` is empty.
- [ ] `GET /api/auth/google/initiate?scopes=unknown` returns `400`.
- [ ] `GET /api/auth/google/callback?state=invalid` returns `400`.
- [ ] `GET /api/auth/google/callback` with an expired `state` token returns `400`.
- [ ] Successful callback creates/updates a `PlannerAccount` row with an encrypted token.

**Tests:**

- [ ] `tests/test_google_oauth.py`:
  - `test_initiate_missing_config()` — expects `503` when client ID is empty.
  - `test_initiate_unknown_scope()` — expects `400`.
  - `test_callback_invalid_state()` — expects `400`.
  - `test_callback_expired_state()` — expects `400`.
  - `test_callback_success()` — mocks `Flow.fetch_token`; asserts `PlannerAccount` upserted.

---

## Step 4 — Google Calendar Token Auto-Refresh

**Files:** `src/janus/connectors/google.py`

**Changes:**

1. Extract a `_get_credentials(self, db) -> Credentials` private method:
   - Loads credentials from `self.decrypted_token`.
   - If `creds.expired and creds.refresh_token`: calls `creds.refresh(Request())`, re-encrypts, and
     writes back to `self.account.token`.
   - Caller must pass a `db` session and call `db.commit()` after.
2. Update `fetch_events()` and `test_connection()` to call `_get_credentials()`.

**Acceptance criteria:**

- [ ] If the stored access token is expired and a refresh token is present, the connector refreshes
  transparently without raising an exception.
- [ ] The refreshed credentials are re-encrypted and saved to the DB.
- [ ] If the refresh token itself is invalid, the connector returns an empty list / `(False, msg)`.

**Tests:**

- [ ] `tests/test_google_oauth.py`:
  - `test_calendar_auto_refresh()` — mocks `creds.expired = True`, `creds.refresh_token = "rt"`,
    and `creds.refresh()`; asserts token is re-written to the account.

---

## Step 5 — Google Tasks Connector

**Files:** `src/janus/connectors/google.py`, `src/janus/planner_service.py`

**Note:** `models.Provider.GOOGLE_TASKS` was already added in Step 1. No new migration needed here.

**Changes:**

1. Implement `GoogleTasksConnector` in `src/janus/connectors/google.py` (see tech spec §6.2).
   - Reuse the `_get_credentials()` helper from `GoogleCalendarConnector`.
2. Register in `PlannerService.get_connector()`:

```python
elif account.provider == models.Provider.GOOGLE_TASKS:
    return google.GoogleTasksConnector(account)
```

**Acceptance criteria:**

- [ ] `GoogleTasksConnector.fetch_tasks()` returns a list of `{"external_id", "title", "status",
  "due_date"}` dicts.
- [ ] `GoogleTasksConnector.fetch_events()` returns `[]`.
- [ ] `GoogleTasksConnector.test_connection()` returns `(True, ...)` on valid credentials.
- [ ] `PlannerService.sync_all()` processes `GOOGLE_TASKS` accounts.

**Tests:**

- [ ] `tests/test_google_oauth.py`:
  - `test_tasks_connector_fetch()` — mocks `googleapiclient.discovery.build`; asserts returned
    task list matches mocked API response.
  - `test_tasks_connector_fetch_events_empty()` — asserts `fetch_events()` returns `[]`.

---

## Step 6 — Gemini LLM Backend

**Files:** `pyproject.toml`, `src/janus/llm_service.py`

**Note:** `models.LLMProvider.GEMINI` and `models.Settings.gemini_api_key` were already added in
Step 1. No new migration needed here.

**Changes:**

1. Add `langchain-google-genai>=2.0.0` to `pyproject.toml` dependencies.
2. Run `uv sync` to lock the new dependency.
3. Add `GEMINI` branch in `LLMService.__init__()` (see tech spec §7.1).

**Acceptance criteria:**

- [ ] Setting `LLMProvider.GEMINI` and providing a valid `gemini_api_key` initializes
  `ChatGoogleGenerativeAI` without error.
- [ ] Missing `gemini_api_key` raises `ValueError("Gemini API key not found in settings.")`.
- [ ] All existing `LLMService` methods (`classify_email`, `generate_briefing`, `stream_briefing`,
  `generate_digest`) work with the Gemini provider. *(Since JAN-231 the briefing and digest methods no
  longer exist; the briefing and digest are trigger runs that use the active provider like chat.)*

**Tests:**

- [ ] `tests/test_llm_service.py` (new test cases):
  - `test_gemini_init_missing_key()` — asserts `ValueError` raised.
  - `test_gemini_init_success()` — mocks `ChatGoogleGenerativeAI`; asserts it is used as `self.llm`.

---

## Step 7 — Frontend (Separate PR)

**Files:** `frontend/src/` (various components), `frontend/src/pages/Settings.tsx`

**Changes:**

1. **Connect Google card** in the Settings page:
   - Button "Connect Google Account" calls `GET /api/auth/google/initiate` and redirects to the
     returned `auth_url`.
   - Status indicator showing active Google scopes (reads `PlannerAccount` rows with
     `provider=GOOGLE_CALENDAR`).
2. **Gemini API key field** in the LLM settings card:
   - Shown only when `llm_provider` is set to `gemini`.
   - Accepts the key and posts it to the settings update endpoint.

**Acceptance criteria:**

- [ ] Clicking "Connect Google Account" opens the Google OAuth consent screen.
- [ ] After completing OAuth, the Settings page shows "Google: Connected".
- [ ] Gemini key field is present and functional when Gemini is selected as the provider.

---

## Rollback Notes

Two Alembic migrations are created across the steps. To roll back one migration:

```bash
uv run alembic downgrade -1
```

| Step | Has migration? | How to revert |
|---|---|---|
| 1 — DB Settings | ✅ `add_google_oauth_and_gemini` | `alembic downgrade -1` |
| 2 — `OAuthState` table | ✅ `add_oauth_state_table` | `alembic downgrade -1` |
| 3 — API endpoints | No | Code revert |
| 4 — Calendar auto-refresh | No | Code revert |
| 5 — Tasks connector | No | Code revert |
| 6 — Gemini backend | No | Code revert + remove `langchain-google-genai` from `pyproject.toml` |
| 7 — Frontend | No | Code revert |

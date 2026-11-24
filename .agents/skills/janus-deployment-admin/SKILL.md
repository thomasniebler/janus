---
name: janus-deployment-admin
description: Administer a live Janus deployment over SSH — reset passwords, create users, link Google accounts, check logs, apply migrations, and manage the supervisor service. Always ask the user for the SSH host alias before connecting; never commit host details to the repository.
---

# Janus Deployment Administration

Use this skill when the user asks to manage users, reset passwords, check logs, restart services, apply migrations, or perform any administrative action on a live Janus deployment.

## Goal

Execute administrative tasks on a remote Janus host via SSH, using the Janus CLI and direct DB access where necessary.

## ⚠️ Critical security rules

1. **Never commit the SSH host, hostname, or any server credentials to the repository.**
   Store the SSH host alias only as a **user-scoped memory** (not repository-scoped).
2. Do not write `.env` files, host names, passwords, or API keys to any file tracked by git.
3. When generating passwords for new users, use `python3 -c "import secrets; print(secrets.token_urlsafe(12))"` locally and report them to the user — never log them in files.

## Inputs

- An administrative action (reset password, create user, restart service, apply migration, …)
- The SSH host alias — **ask the user** if not already stored in user memory

## Workflow

### Step 0 — Resolve the SSH host

Check user-scoped memory for a stored deployment SSH alias (key: `janus_deploy_ssh_host`).
- If found: use it and confirm with the user ("Using `<alias>` — is that right?").
- If not found: use the `ask_user` tool to ask:
  > "What is the SSH host alias for your Janus deployment (as it appears in `~/.ssh/config`)?"
  Then store the answer as a **user-scoped** memory with subject `deployment` and fact
  `Janus production SSH host alias is "<alias>"`.
  **Do not store it as a repository memory.**

### Step 1 — Determine the Janus port

The Janus server may run on a non-default port (the default CLI assumes 8080).
Check by inspecting the supervisor config:

```bash
ssh <host> 'cat ~/etc/services.d/janus.ini | grep port'
```

Extract the `--port <N>` value and use `--url http://localhost:<N>` for all CLI calls.

### Step 2 — Use the Janus CLI for user operations

Prefer the CLI over direct DB access whenever possible — it handles hashing, validation, and API consistency.

```bash
# List users
ssh <host> 'cd ~/janus && uv run janus --url http://localhost:<PORT> user list'

# Create a new user (generates and passes a secure password)
PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
ssh <host> "cd ~/janus && uv run janus --url http://localhost:<PORT> user create <username> --password '$PASSWORD'"

# Bootstrap the very first admin (bypasses the API — writes directly to DB)
ssh <host> "cd ~/janus && uv run janus user bootstrap-admin <username> --password '<password>'"
```

> **Note:** `bootstrap-admin` writes directly to the SQLite/Postgres DB and does not require a running server. Use it only for the initial admin setup or when the server is not reachable.

### Step 3 — Password resets (no CLI reset command)

When a password reset is needed, reset it via `uv run python` in the project venv so that the project's `bcrypt` dependency is available:

```bash
ssh <host> "cd ~/janus && uv run python -c \"
import bcrypt, sqlite3
pw = '<NEW_PASSWORD>'.encode()
hashed = bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode()
db = '/home/<USER>/.local/share/janus/janus.db'
conn = sqlite3.connect(db)
conn.execute('UPDATE users SET hashed_password=? WHERE username=?', (hashed, '<USERNAME>'))
conn.commit()
print('Updated. Rows changed:', conn.total_changes)
conn.close()
\""
```

Replace `<USER>` with the Unix user on the server (e.g. `janus`).

> For PostgreSQL-backed deployments, use `psycopg2` or `sqlalchemy` instead of `sqlite3`, and connect with the DB URL from the server's environment.

### Step 4 — Link a Google account to a user

After the user completes a Google login attempt (which will fail with `google_user_not_linked`), find their `google_id` from the server logs, then call the admin API:

```bash
# Find google_id from supervisor logs
ssh <host> 'supervisorctl tail janus | grep google_id'

# Link via admin API (requires an active admin session cookie — easier via curl with login)
TOKEN=$(curl -s -c /tmp/janus_cookies.txt -X POST http://localhost:<PORT>/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"<ADMIN>\",\"password\":\"<ADMIN_PW>\"}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

curl -s -b /tmp/janus_cookies.txt \
  -X POST http://localhost:<PORT>/api/auth/users/<USERNAME>/link-google \
  -H "Content-Type: application/json" \
  -d "{\"google_id\": \"<GOOGLE_SUB>\"}"
```

Or retrieve the google_id from the callback URL parameters visible in browser dev-tools (the `sub` field in the Google ID token).

### Step 5 — Apply database migrations

```bash
ssh <host> 'cd ~/janus && uv run alembic upgrade head'
```

Always check the current revision first:

```bash
ssh <host> 'cd ~/janus && uv run alembic current'
```

### Step 6 — Restart the service

Janus runs under `supervisord` on Uberspace:

```bash
ssh <host> 'supervisorctl restart janus'
ssh <host> 'supervisorctl status janus'
```

Tail logs:

```bash
ssh <host> 'supervisorctl tail -f janus'
```

### Step 7 — Verify the deployment

```bash
# Health check
curl -s http://localhost:<PORT>/api/health | python3 -m json.tool

# Check DB location and size
ssh <host> 'ls -lh ~/.local/share/janus/janus.db'

# Confirm users
ssh <host> 'sqlite3 ~/.local/share/janus/janus.db "SELECT id, username, is_admin FROM users;"'
```

## Uberspace-specific notes

- The supervisor config lives at `~/etc/services.d/janus.ini`.
- The data directory is `~/.local/share/janus/` (SQLite DB + `.janus_key` Fernet key).
- The `janus.db` in `~/janus/` is a **zero-byte placeholder** — the real DB is in the data dir above.
- Restart with `supervisorctl restart janus` (no `sudo` needed on Uberspace).
- The `uv` binary is at `~/.local/bin/uv`; all Python tasks run inside the project venv via `uv run`.
- The server port is defined in `~/etc/services.d/janus.ini` — check it with `grep port ~/etc/services.d/janus.ini`.

## Example triggers

- "Reset thomas's password on prod"
- "Create a new user on the deployment"
- "Apply the latest migration to production"
- "Link my Google account on the server"
- "Restart janus on the remote host"
- "What users exist on the live deployment?"

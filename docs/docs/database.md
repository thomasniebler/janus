# Database Configuration

Janus supports two database backends: the built-in **SQLite** default and an optional **PostgreSQL** instance. Both share the same schema and migration workflow; only the connection URL and driver differ. CI runs the full migration chain against PostgreSQL in the nightly run on `main` and on every ready-for-review pull request that changes the schema, the migrations, or the modules they import.

---

## SQLite (default)

By default, Janus stores SQLite on a persistent absolute path under the XDG data directory:

```bash
sqlite:////home/you/.local/share/janus/janus.db
```

The Fernet key and runtime logs should also live on persistent absolute paths:

```bash
JANUS_FERNET_KEY_PATH=/home/you/.local/share/janus/.janus_key
JANUS_LOG_FILE_PATH=/home/you/.local/state/janus/janus.log
```

```bash
uv run janus start --server-mode
```

SQLite is ideal for single-machine, single-user setups where no remote access is needed.
Janus enforces a **single running server instance** per configured runtime directory/database
so the SQLite database and in-process workers are not shared concurrently.

Semantic memory and topic search (see [Configuration](configuration.md#semantic-search-for-memory-and-topics))
loads the `sqlite-vec` extension, which needs a Python whose `sqlite3` module supports
extension loading. Check with:

```bash
uv run python -c "import sqlite3; print(hasattr(sqlite3.connect(':memory:'), 'enable_load_extension'))"
```

`False` is not fatal: search falls back to keyword matching, and **Status** reports it.
Where Python's SQLite has FTS5, Janus also keeps FTS5 keyword tables (`knowledge_fts_*`)
outside the Alembic schema; they rebuild automatically if dropped. Without FTS5 (for example
the uv-managed Python on Uberspace), keyword search matches substrings instead.

Ingested documents keep only their extracted text in `knowledge_chunks`. Attachments waiting to be
parsed hold their bytes in `knowledge_sources` until the next worker pass, capped at 200 MB in total.

If the host system Python is older than 3.12, provision a managed runtime first:

```bash
uv python install 3.12
uv run --python 3.12 janus start --server-mode
```

---

## PostgreSQL

Use PostgreSQL when you want persistent storage that survives restarts, multiple client connections, or a database hosted on a remote server (e.g. a VPS).

!!! note "Semantic search is SQLite-only for now"
    On PostgreSQL, memory and topic search match substrings: there is no vector index for PostgreSQL yet, so no embeddings are computed even with an embedding model configured, and **Status** shows **Knowledge search** as degraded. Stored embeddings are copied by `janus db copy` and are used again if you move back to SQLite.

### 1. Install the driver

The PostgreSQL driver (`psycopg2-binary`) is the optional `postgres` extra:

```bash
uv sync --extra postgres          # git checkout
uv tool install "janus-assistant[postgres]"     # tool install
```

Without it, `janus config doctor` reports that `DATABASE_URL` points to PostgreSQL but the driver is missing.

### 2. Set the connection URL

Janus reads the database backend from the `DATABASE_URL` environment variable. Set it to a standard SQLAlchemy PostgreSQL URL:

```
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>
```

The recommended approach is to copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# edit .env — fill in DATABASE_URL and JANUS_SECRET_KEY
```

`.env` is gitignored and will never be committed. `.env.example` contains redacted placeholders and is safe to track.

### 3. Run migrations

Apply the schema to your PostgreSQL database before starting Janus:

```bash
uv run alembic upgrade head
```

Migrations are fully idempotent — re-running them on an existing database is safe.

### 4. Move existing data (optional)

To move an existing SQLite installation to PostgreSQL, stop Janus and copy everything into the
empty PostgreSQL database. The command migrates the target to the current schema first, refuses
to write into a database that already holds Janus data, and copies inside one transaction:

```bash
uv run janus db copy \
  --source sqlite:////home/you/.local/share/janus/janus.db \
  --target postgresql://<user>:<password>@<host>:<port>/<database>
```

`--source` defaults to the configured `DATABASE_URL`. The source must already be at the current
schema revision (start Janus against it once if it is not). Keep the same Fernet key file
(`JANUS_FERNET_KEY_PATH`): stored credentials are copied still encrypted. WhatsApp pairing
sessions live in their own Neonize session files, not in the Janus database, and stay where they are.

### 5. Start Janus

```bash
uv run janus start
# or
task run
```

---

## SSH tunnel for a remote database

If your PostgreSQL instance is on a remote host that is not directly reachable (e.g. a VPS accessible only via SSH), open a local tunnel first.

A convenience task is provided in `Taskfile.yml`:

```bash
# Terminal 1 — keep this open while Janus is running
task tunnel:db
```

This forwards a local port to the remote PostgreSQL port over SSH. Configure the tunnel target in `Taskfile.yml` to match your server.

Your `DATABASE_URL` should then point to `localhost` at the forwarded port:

```
DATABASE_URL=postgresql://<user>:<password>@localhost:<local-port>/<database>
```

> **Tip:** If your machine already runs a local PostgreSQL instance on the standard port, use a different local port for the tunnel (e.g. `15432`) to avoid conflicts.

---

## Choosing a port

If port `5432` is already in use on your machine, the tunnel task uses an alternate local port. Update the tunnel task in `Taskfile.yml` and the port number in your `DATABASE_URL` to match.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | No (defaults to SQLite) | SQLAlchemy connection URL for the database backend |
| `JANUS_FERNET_KEY_PATH` | No | Absolute path of the Fernet key file |
| `JANUS_LOG_FILE_PATH` | No | Absolute path of the runtime log file |
| `JANUS_SERVER_MODE` | No | Headless deployment mode for shared hosting/servers |
| `JANUS_SECRET_KEY` | Yes (production) | Secret key for JWT signing and session security |

See `.env.example` for a template.

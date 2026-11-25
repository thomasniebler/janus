"""Typer CLI commands for Janus."""

import json
import logging
import sys
import threading

import typer
import uvicorn

from janus import cli_client, database, models, notifications, status_service
from janus.app_factory import api
from janus.cli_microsoft import microsoft_app
from janus.cli_service import service_app
from janus.cli_update import update
from janus.config import _SETTINGS_TEMPLATE, _normalize_allowed_origin, get_config_file_path, settings
from janus.desktop.tray import create_tray_icon, set_tray_dashboard_url
from janus.processor import validate_regex
from janus.services import auth_service, update_boot
from janus.services.database_copy_service import DatabaseCopyError, copy_database
from janus.services.topic_service import TopicService
from janus.startup import ensure_runtime_initialized
from janus.worker_manager import worker_manager
from janus.workers import update as update_worker
from janus.workers.email import initial_sync_worker
from janus.workers.errors import error_compaction_loop
from janus.workers.planner import planner_sync_loop
from janus.workers.whatsapp import start_account_connector

logger = logging.getLogger("janus")

app = typer.Typer(help="Janus: Your personal AI assistant for communication and planning.")
client = cli_client.JanusClient()


@app.callback()
def main(
    url: str = typer.Option("http://localhost:8080", help="Janus backend API URL", envvar="JANUS_URL"),
):
    """Set the global API URL."""
    client.base_url = url.rstrip("/")


account_app = typer.Typer(help="Manage email accounts for monitoring.")
rule_app = typer.Typer(help="Manage rules for email prioritization.")
planner_app = typer.Typer(help="Manage planner integrations (Google, Jira, etc.).")
user_app = typer.Typer(help="Manage system users.")
config_app = typer.Typer(help="Manage Janus user configuration.")
db_app = typer.Typer(help="Manage the Janus database.")
knowledge_app = typer.Typer(help="Manage semantic search over memory facts and topics.")
app.add_typer(account_app, name="account")
app.add_typer(rule_app, name="rule")
app.add_typer(planner_app, name="planner")
app.add_typer(user_app, name="user")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(microsoft_app, name="microsoft")
app.add_typer(service_app, name="service")
app.command()(update)


def _compat_main_attr(name: str, fallback):
    """Return a legacy ``janus.main`` attribute when tests/extensions patch it."""
    main_module = sys.modules.get("janus.main")
    if main_module is None:
        return fallback
    return getattr(main_module, name, fallback)


@app.command()
def login(
    username: str = typer.Argument(..., help="Janus username"),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Janus password"),
):
    """Log in to the Janus backend and save credentials locally."""
    if client.login(username, password):
        typer.secho("Successfully logged in!", fg=typer.colors.GREEN)
    else:
        typer.secho("Login failed. Please check your credentials.", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def me():
    """Show information about the current logged-in user."""
    user = client.get_me()
    if user:
        typer.echo(f"Logged in as: {user['username']}")
    else:
        typer.secho("Not logged in or session expired.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)


@app.command("desktop-summary")
def desktop_summary(
    include_unread_emails: bool = typer.Option(
        True,
        "--include-unread-emails/--no-unread-emails",
        help="Include the current user's unread email count.",
    ),
):
    """Print Waybar/Sway-compatible JSON from the Janus API."""
    try:
        summary = client.get_desktop_summary(include_unread_emails=include_unread_emails)
        open_tasks = int(summary.get("open_tasks", 0) or 0)
        unread_emails = summary.get("unread_emails")
        parts = [f"{open_tasks} tasks"]
        if unread_emails is not None:
            parts.append(f"{int(unread_emails)} unread")
        payload = {
            "text": " · ".join(parts),
            "alt": "Janus",
            "class": "normal",
            "tooltip": "Janus: " + ", ".join(parts),
        }
    except Exception as exc:
        logger.warning("Desktop summary unavailable: %s", exc)
        payload = {
            "text": "Janus unavailable",
            "alt": "Janus unavailable",
            "class": "unavailable",
            "tooltip": "Janus desktop summary could not be loaded.",
        }
    typer.echo(json.dumps(payload))


@app.command()
def start(
    host: str = typer.Option(settings.host, help="Host interface to bind to"),
    port: int = typer.Option(settings.port, help="Port for the dashboard"),
    tray: bool = typer.Option(False, "--tray/--no-tray", help="Enable system tray icon"),
    server_mode: bool = typer.Option(
        settings.server_mode,
        "--server-mode/--desktop-mode",
        help="Disable desktop-only integrations for headless/shared-hosting deployments.",
    ),
):
    """Start the Janus background monitoring agent and dashboard."""
    with update_boot.recover_on_failed_boot():
        _serve(host, port, tray, server_mode)


def _serve(host: str, port: int, tray: bool, server_mode: bool) -> None:
    try:
        ensure_runtime_initialized()
    except RuntimeError as exc:
        typer.secho(f"{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    tray_dashboard_url = settings.base_url
    if host != settings.host or port != settings.port:
        tray_dashboard_url = f"http://{host}:{port}"
    set_tray_dashboard_url(tray_dashboard_url)

    notifications.NotificationService.configure(enabled=not server_mode)
    if server_mode and tray:
        logger.info("Ignoring --tray because server mode disables desktop integrations.")
        tray = False

    db = database.SessionLocal()
    try:
        accounts = db.query(models.EmailAccount).filter(models.EmailAccount.is_active).all()
        for account in accounts:
            worker_manager.start(f"imap:{account.email}", initial_sync_worker, args=(account,))

        whatsapp_accounts = db.query(models.WhatsAppAccount).filter(models.WhatsAppAccount.is_enabled.is_(True)).all()
        for whatsapp_account in whatsapp_accounts:
            start_account_connector(whatsapp_account)
    finally:
        db.close()

    worker_manager.start("error-compaction", error_compaction_loop, restart_policy=True)
    worker_manager.start("planner", planner_sync_loop, restart_policy=True)
    worker_manager.start("llm-status-probe", status_service.llm_status_probe_loop, restart_policy=True)
    update_worker.start_if_eligible()
    if tray:
        threading.Thread(target=create_tray_icon, daemon=True).start()

    typer.secho(f"Janus is starting up. API responding soon at http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    # uvicorn trusts X-Forwarded-* from 127.0.0.1 by default, which would let any loopback
    # client bypass TrustedProxyHeadersMiddleware's trusted_proxy_hops gating; disable it so
    # that middleware is the sole authority on forwarded-header trust.
    server = uvicorn.Server(uvicorn.Config(api, host=host, port=port, log_level="info", proxy_headers=False))
    update_boot.attach_server(server)
    server.run()


def _run_builtin_trigger_cli(builtin_key: str, heading: str, color: str) -> None:
    """Run one built-in trigger definition inline for the default user and print its answer."""
    from janus import builtin_triggers
    from janus import trigger_definition_service as definitions
    from janus.trigger_runtime import run_definition_inline

    database.ensure_schema_up_to_date()
    db = database.SessionLocal()
    try:
        default_user = TopicService(db).get_default_user()
        if default_user is None:
            typer.secho("No default user found. Please bootstrap an admin user first.", fg=typer.colors.RED)
            return
        builtin_triggers.ensure_builtin_definitions(db, default_user.id)
        definition = definitions.get_builtin_definition(db, default_user.id, builtin_key)
        definition_id = definition.id if definition is not None else None
    finally:
        db.close()
    if definition_id is None:
        typer.secho(f"The {builtin_key} trigger is missing.", fg=typer.colors.RED)
        return
    record = run_definition_inline(database.SessionLocal, definition_id)
    if record.status != models.RuntimeTriggerStatus.COMPLETED.value:
        typer.secho(f"The run failed: {record.last_error or record.status}", fg=typer.colors.RED)
        return
    typer.secho(f"\n{heading}", fg=color, bold=True)
    typer.echo(record.output or "")


@app.command()
def brief():
    """Run the Morning briefing trigger now and print the briefing."""
    _run_builtin_trigger_cli("builtin.briefing", "Janus Says:", typer.colors.GREEN)


@app.command()
def digest():
    """Run the Afternoon digest trigger now (emails since the last digest) and print it."""
    _run_builtin_trigger_cli("builtin.digest", "Afternoon Digest", typer.colors.MAGENTA)


@account_app.command("add")
def add_account(
    name: str = typer.Argument(...),
    email: str = typer.Argument(...),
    username: str = typer.Argument(...),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    imap_server: str = typer.Option("imap.gmail.com"),
    imap_port: int = typer.Option(993),
):
    """Add a new email account."""
    try:
        data = {
            "name": name,
            "email": email,
            "username": username,
            "password": password,
            "imap_server": imap_server,
            "imap_port": imap_port,
        }
        client.add_account(data)
        typer.secho(f"Account '{name}' added successfully.", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@account_app.command("list")
def list_accounts():
    """List all email accounts."""
    try:
        for acc in client.list_accounts():
            typer.echo(f"{acc['id']}: {acc['name']} ({acc['email']})")
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@rule_app.command("add")
def add_rule(name: str, pattern: str, field: str = "sender", priority: models.Priority = models.Priority.IMPORTANT):
    """Add a new rule."""
    if not validate_regex(pattern):
        typer.secho("Invalid regex", fg=typer.colors.RED)
        return
    try:
        client.add_rule({"name": name, "pattern": pattern, "field": field, "priority": priority.value})
        typer.secho(f"Rule '{name}' added.", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@rule_app.command("list")
def list_rules():
    """List all rules."""
    try:
        for r in client.list_rules():
            typer.echo(f"{r['id']}: {r['name']} ({r['priority']})")
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@planner_app.command("add")
def add_planner(
    name: str,
    provider: models.Provider,
    token: str = typer.Option(..., prompt=True, hide_input=True),
    api_url: str | None = None,
):
    """Add a planner."""
    try:
        client.add_planner({"name": name, "provider": provider.value, "token": token, "api_url": api_url})
        typer.secho(f"Planner '{name}' added.", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@planner_app.command("list")
def list_planners():
    """List planners."""
    try:
        for p in client.list_planners():
            typer.echo(f"{p['id']}: {p['name']} ({p['provider']})")
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@user_app.command("create")
def create_user(username: str, password: str = typer.Option(..., prompt=True, hide_input=True)):
    """Create a user."""
    try:
        client.request("POST", "/api/auth/users", json={"username": username, "password": password})
        typer.secho(f"User '{username}' created.", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@db_app.command("copy")
def copy_db(
    target: str = typer.Option(..., "--target", help="SQLAlchemy URL of the empty target database."),
    source: str = typer.Option(
        None, "--source", help="SQLAlchemy URL of the source database. Defaults to the configured DATABASE_URL."
    ),
):
    """Copy all Janus data into an empty database, e.g. from SQLite to PostgreSQL.

    Stop the Janus server first so the source does not change during the copy.
    """
    try:
        result = copy_database(
            source or settings.database_url,
            target,
            on_table=lambda table, rows: typer.echo(f"  {table}: {rows}"),
        )
    except DatabaseCopyError as exc:
        typer.secho(f"Failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.secho(
        f"Copied {result.total_rows} rows. Point DATABASE_URL at the target and keep the same Fernet key file.",
        fg=typer.colors.GREEN,
    )


@knowledge_app.command("backfill")
def knowledge_backfill():
    """Ingest all existing mail, chats, WhatsApp chats, planner tasks, and calendar events as knowledge sources.

    Runs until nothing is pending; the server's knowledge-index worker does the
    same in bounded batches. Attachments of mail ingested before this feature
    cannot be recovered. Embeddings follow with ``janus knowledge reembed`` or
    the worker once an embedding model is configured.
    """
    from janus.services import knowledge_sources

    db = database.SessionLocal()
    try:
        result = knowledge_sources.backfill(db)
    finally:
        db.close()
    typer.echo(f"Processed {result.processed} sources, {result.failed} failed, {result.deleted} removed.")
    if result.errors:
        typer.secho(f"Failed: {result.errors[0]}", fg=typer.colors.RED)
        raise typer.Exit(1)


@knowledge_app.command("reembed")
def knowledge_reembed(
    force: bool = typer.Option(False, "--force", help="Re-embed every fact and topic, not only missing or stale ones."),
):
    """Embed all missing or stale memory facts and topics with the configured embedding model.

    Also rebuilds the keyword index with ``--force``. Runs until nothing is
    stale; the server's knowledge-index worker does the same in small batches.
    """
    from janus.services import knowledge_index, vector_index

    db = database.SessionLocal()
    try:
        if force:
            vector_index.drop_keyword_tables(db)
            keyword_index = vector_index.get_keyword_index(db)
            if keyword_index is not None:
                keyword_index.ensure_tables(db)
            db.commit()
        result = knowledge_index.sweep(db, max_batches=None, force=force)
    finally:
        db.close()
    if result.skipped_reason:
        typer.secho(f"Skipped: {result.skipped_reason}.", fg=typer.colors.YELLOW)
        return
    typer.echo(f"Embedded {result.embedded}, refreshed {result.refreshed} unchanged.")
    if result.errors:
        typer.secho(f"Failed: {result.errors[0]}", fg=typer.colors.RED)
        raise typer.Exit(1)


@user_app.command("bootstrap-admin")
def bootstrap_admin(
    username: str,
    password: str = typer.Option(
        ...,
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Password for the first admin account.",
    ),
):
    """Create the first admin user directly in the local database."""
    try:
        database.ensure_schema_up_to_date()
        db = database.SessionLocal()
        try:
            auth_service.bootstrap_initial_admin(db, username, password)
        finally:
            db.close()
        typer.secho(
            f"Bootstrapped admin user '{username}'. You can now log in to the dashboard.",
            fg=typer.colors.GREEN,
        )
    except Exception as exc:
        typer.secho(f"Failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@user_app.command("list")
def list_users():
    """List all system users."""
    try:
        users = client.request("GET", "/api/auth/users")
        if not users:
            typer.echo("No users found.")
            return

        typer.secho(f"{'ID':<5} {'Username':<20} {'Admin':<10}", bold=True)
        typer.echo("-" * 40)
        for u in users:
            admin_str = "Yes" if u.get("is_admin") else "No"
            typer.echo(f"{u['id']:<5} {u['username']:<20} {admin_str:<10}")
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED)


@config_app.command("init")
def config_init():
    """Create ~/.config/janus/settings.toml with a commented template."""
    config_path = _compat_main_attr("get_config_file_path", get_config_file_path)()
    if config_path.exists():
        typer.secho(f"Config file already exists at {config_path}; not overwriting.", fg=typer.colors.YELLOW)
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_SETTINGS_TEMPLATE, encoding="utf-8")
    typer.secho(f"Created {config_path}", fg=typer.colors.GREEN)
    typer.secho(
        f"Edit {config_path} to set your persistent configuration.",
        fg=typer.colors.CYAN,
    )


@config_app.command("show")
def config_show():
    """Print the effective resolved configuration with sensitive values masked."""
    active_settings = _compat_main_attr("settings", settings)
    config_path = _compat_main_attr("get_config_file_path", get_config_file_path)()
    if config_path.exists():
        typer.secho(f"# Loaded from: {config_path}", fg=typer.colors.CYAN)
    else:
        typer.secho("# No user config file found; using defaults and environment variables.", fg=typer.colors.YELLOW)
    for key, value in active_settings.show().items():
        typer.echo(f'{key} = "{value}"' if isinstance(value, str) else f"{key} = {value}")


@config_app.command("doctor")
def config_doctor():  # noqa: C901
    """Check the active configuration for common misconfigurations."""
    from urllib.parse import urlsplit

    active_settings = _compat_main_attr("settings", settings)
    errors_found: list[str] = []
    warnings_found: list[str] = []

    def normalize_origin_host(host: str) -> str:
        """Canonicalize origin hosts for doctor checks."""
        if host in ("localhost", "127.0.0.1", "::1"):
            return "localhost"
        if ":" in host:
            return f"[{host}]"
        return host

    if active_settings.is_placeholder_key:
        errors_found.append(
            "secret_key is still the default placeholder. Generate a secure value with: openssl rand -hex 32"
        )

    parsed_base = urlsplit(active_settings.base_url)
    base_scheme = parsed_base.scheme or "http"
    base_host = parsed_base.hostname or "localhost"
    default_port = 443 if base_scheme == "https" else 80
    base_url_port = parsed_base.port or default_port

    if base_host in ("localhost", "127.0.0.1", "::1") and base_url_port != active_settings.port:
        errors_found.append(
            f"port ({active_settings.port}) does not match the port in base_url ({active_settings.base_url}). "
            "OAuth redirect URIs and generated links will point to the wrong address. "
            f"Either set port = {base_url_port} or update base_url to use port {active_settings.port}."
        )

    public_origin_host = normalize_origin_host(base_host)
    if base_url_port in (80, 443):
        raw_public_origin = f"{base_scheme}://{public_origin_host}"
    else:
        raw_public_origin = f"{base_scheme}://{public_origin_host}:{base_url_port}"

    try:
        normalized_public_origin = _normalize_allowed_origin(raw_public_origin)
    except ValueError:
        normalized_public_origin = raw_public_origin

    if normalized_public_origin not in active_settings.allowed_origins:
        errors_found.append(
            f"The public server origin ({normalized_public_origin}) derived from base_url is not in allowed_origins. "
            "CORS and WebSocket connections from the built-in dashboard will be rejected. "
            f'Add it: allowed_origins = ["{normalized_public_origin}"]'
        )

    bind_origin_host = (
        "localhost" if active_settings.host in ("0.0.0.0", "127.0.0.1", "::", "::1") else active_settings.host
    )
    bind_origin_host = normalize_origin_host(bind_origin_host)
    bind_origin_port = active_settings.port
    if bind_origin_port in (80, 443):
        raw_bind_origin = f"{base_scheme}://{bind_origin_host}"
    else:
        raw_bind_origin = f"{base_scheme}://{bind_origin_host}:{bind_origin_port}"

    try:
        normalized_bind_origin = _normalize_allowed_origin(raw_bind_origin)
    except ValueError:
        normalized_bind_origin = raw_bind_origin

    if (
        normalized_bind_origin != normalized_public_origin
        and normalized_bind_origin not in active_settings.allowed_origins
    ):
        warnings_found.append(
            f"The bind-address origin ({normalized_bind_origin}) is not in allowed_origins. "
            "This is usually fine when Janus is accessed via base_url, but direct access to the bind "
            "address may fail CORS/WebSocket checks."
        )

    allowed_hostnames = {urlsplit(f"http://{h}").hostname for h in active_settings.allowed_hosts}
    if base_host not in allowed_hostnames and base_host != "localhost":
        warnings_found.append(
            f"The hostname from base_url ({base_host}) is not in allowed_hosts. "
            "Requests arriving with that Host header may be rejected with 400 Bad Request. "
            f'Add it: allowed_hosts = ["{base_host}"]'
        )

    errors_found.extend(database.driver_problems(active_settings.database_url))

    if not active_settings.fernet_key_path.exists():
        warnings_found.append(
            f"Fernet key file not found at {active_settings.fernet_key_path}. "
            "Stored credentials cannot be decrypted until the server has been started once."
        )

    if active_settings.server_mode and base_scheme == "http" and base_host not in ("localhost", "127.0.0.1", "::1"):
        warnings_found.append(
            "base_url uses plain HTTP in server_mode for a non-localhost host. Consider using HTTPS in production."
        )

    if active_settings.server_mode and base_host in ("localhost", "127.0.0.1", "::1"):
        warnings_found.append(
            "server_mode is enabled but base_url still points to localhost. "
            "For a public deployment, update base_url to your public hostname."
        )

    if not errors_found and not warnings_found:
        typer.secho("\u2705 No configuration issues found.", fg=typer.colors.GREEN, bold=True)
        return

    if errors_found:
        typer.secho(f"{len(errors_found)} error(s) found:", fg=typer.colors.RED, bold=True)
        for msg in errors_found:
            typer.secho(f"   - {msg}", fg=typer.colors.RED)
        typer.echo()

    if warnings_found:
        typer.secho(f"{len(warnings_found)} warning(s) found:", fg=typer.colors.YELLOW, bold=True)
        for msg in warnings_found:
            typer.secho(f"   - {msg}", fg=typer.colors.YELLOW)
        typer.echo()

    if errors_found:
        raise typer.Exit(1)

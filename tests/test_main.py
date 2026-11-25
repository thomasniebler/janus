"""Tests for main entrypoint helpers."""

import builtins
import importlib
import importlib.util
import inspect
import json
import multiprocessing
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from asgi_client import SyncASGIClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from janus import database, models, runtime
from janus.security import security_service


def test_main_import_and_tray_creation_are_safe_without_pystray_in_headless_mode(monkeypatch):
    """Headless environments should not depend on importing pystray."""
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "pystray":
            raise ModuleNotFoundError("No module named 'pystray'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delitem(sys.modules, "pystray", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("janus.main", None)

    main = importlib.import_module("janus.main")

    main.create_tray_icon()


def test_desktop_summary_prints_waybar_json(monkeypatch):
    """The desktop command stays valid JSON and bar-friendly."""
    main = importlib.import_module("janus.main")
    monkeypatch.setattr(
        main.client,
        "get_desktop_summary",
        lambda include_unread_emails=True: {
            "open_tasks": 3,
            "unread_emails": 2,
        },
    )
    result = CliRunner().invoke(main.app, ["desktop-summary"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["text"] == "3 tasks · 2 unread"


def test_desktop_summary_falls_back_to_valid_json(monkeypatch):
    """A stopped backend must not break Waybar's JSON parser."""
    main = importlib.import_module("janus.main")
    monkeypatch.setattr(main.client, "get_desktop_summary", lambda **_: (_ for _ in ()).throw(OSError("offline")))
    result = CliRunner().invoke(main.app, ["desktop-summary"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["class"] == "unavailable"


def test_login_persists_bearer_token_for_desktop_summary(client, db, monkeypatch, tmp_path):
    """CLI login should persist a bearer token that the desktop-summary command can reuse."""
    main = importlib.import_module("janus.main")
    runner = CliRunner()
    token_file = tmp_path / "janus-token.json"

    hashed_pw = security_service.get_password_hash("secret-pass")
    user = models.User(username="cli-user", hashed_password=hashed_pw, is_admin=False)
    other_user = models.User(
        username="other-user",
        hashed_password=security_service.get_password_hash("other-pass"),
        is_admin=False,
    )
    db.add(user)
    db.add(other_user)
    db.commit()

    current_email_account = models.EmailAccount(
        name="CLI Inbox",
        email="cli-user@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="cli-user",
        password="encrypted",
        user_id=user.id,
    )
    other_email_account = models.EmailAccount(
        name="Other Inbox",
        email="other@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="other",
        password="encrypted",
        user_id=other_user.id,
    )
    db.add_all(
        [
            current_email_account,
            other_email_account,
        ]
    )
    db.flush()
    db.add_all(
        [
            models.PlannerTask(user_id=user.id, title="Open task", status=models.TaskStatus.TODO),
            models.PlannerTask(user_id=user.id, title="Done task", status=models.TaskStatus.DONE),
            models.PlannerTask(user_id=other_user.id, title="Other task", status=models.TaskStatus.TODO),
            models.EmailRecord(
                account_id=current_email_account.id,
                message_id="cli-unread",
                subject="Unread",
                sender="sender@example.com",
                snippet="unread",
                is_read=False,
            ),
            models.EmailRecord(
                account_id=current_email_account.id,
                message_id="cli-read",
                subject="Read",
                sender="sender@example.com",
                snippet="read",
                is_read=True,
            ),
            models.EmailRecord(
                account_id=other_email_account.id,
                message_id="other-unread",
                subject="Other",
                sender="other@example.com",
                snippet="other",
                is_read=False,
            ),
        ]
    )
    db.commit()

    with SyncASGIClient(main.api) as api_client:
        import janus.cli_client as cli_client_module

        monkeypatch.setattr(cli_client_module, "TOKEN_FILE", str(token_file))
        main.client.token = None
        main.client.base_url = "http://testserver"

        def fake_post(url, **kwargs):
            return api_client.post(url, **kwargs)

        def fake_request(method, url, **kwargs):
            return api_client.request(method, url, **kwargs)

        monkeypatch.setattr(cli_client_module.requests, "post", fake_post)
        monkeypatch.setattr(cli_client_module.requests, "request", fake_request)

        login_result = runner.invoke(main.app, ["login", "cli-user", "--password", "secret-pass"])
        assert login_result.exit_code == 0, login_result.output

        desktop_result = runner.invoke(main.app, ["desktop-summary"])
        assert desktop_result.exit_code == 0, desktop_result.output
        assert json.loads(desktop_result.stdout) == {
            "text": "1 tasks · 1 unread",
            "alt": "Janus",
            "class": "normal",
            "tooltip": "Janus: 1 tasks, 1 unread",
        }


def test_create_tray_icon_uses_pystray_in_graphical_sessions(monkeypatch):
    """Graphical sessions should still create and run the tray icon."""
    pytest.importorskip("PIL", reason="needs the optional 'desktop' extra")
    icon = MagicMock()
    pystray_stub = ModuleType("pystray")
    pystray_stub.Icon = MagicMock(return_value=icon)
    pystray_stub.Menu = MagicMock(side_effect=lambda *items: items)
    pystray_stub.MenuItem = MagicMock(side_effect=lambda label, callback: (label, callback))

    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setitem(sys.modules, "pystray", pystray_stub)

    main = importlib.import_module("janus.main")

    main.create_tray_icon()

    pystray_stub.Icon.assert_called_once()
    icon.run.assert_called_once()


def test_start_registers_error_compaction_worker(monkeypatch):
    """Starting Janus should register the error compaction worker alongside other background loops."""
    main = importlib.import_module("janus.main")

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    class FakeSession:
        def query(self, model):
            assert model in (models.EmailAccount, models.WhatsAppAccount)
            return FakeQuery()

        def close(self):
            return None

    start_mock = MagicMock()
    config_mock = MagicMock()
    server_mock = MagicMock()

    monkeypatch.setattr(main, "ensure_runtime_initialized", lambda: None)
    monkeypatch.setattr(main.database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(main.worker_manager, "start", start_mock)
    monkeypatch.setattr(main.uvicorn, "Config", config_mock)
    monkeypatch.setattr(main.uvicorn, "Server", server_mock)

    main.start(host="127.0.0.1", port=8080, tray=False)

    labels = [call.args[0] for call in start_mock.call_args_list]
    assert "error-compaction" in labels
    assert "planner" in labels
    assert "llm-status-probe" in labels
    config_mock.assert_called_once_with(main.api, host="127.0.0.1", port=8080, log_level="info", proxy_headers=False)
    server_mock.return_value.run.assert_called_once_with()


def test_start_server_mode_disables_desktop_integrations(monkeypatch):
    """Server mode should keep tray and desktop notifications disabled."""
    main = importlib.import_module("janus.main")

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    class FakeSession:
        def query(self, model):
            assert model in (models.EmailAccount, models.WhatsAppAccount)
            return FakeQuery()

        def close(self):
            return None

    configure_mock = MagicMock()
    thread_mock = MagicMock()

    monkeypatch.setattr(main, "ensure_runtime_initialized", lambda: None)
    monkeypatch.setattr(main.database, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(main.notifications.NotificationService, "configure", configure_mock)
    monkeypatch.setattr(main.worker_manager, "start", MagicMock())
    monkeypatch.setattr(main.uvicorn, "Server", MagicMock())
    monkeypatch.setattr(main.threading, "Thread", thread_mock)

    main.start(host="127.0.0.1", port=8080, tray=True, server_mode=True)

    configure_mock.assert_called_once_with(enabled=False)
    targets = [call.kwargs.get("target") for call in thread_mock.call_args_list]
    assert main.create_tray_icon not in targets


def _acquire_lock(lock_path: str, queue: multiprocessing.Queue) -> None:
    """Child-process helper for validating runtime lock contention."""
    lock = runtime.SingleInstanceLock(Path(lock_path))
    try:
        lock.acquire()
    except RuntimeError as exc:
        queue.put(str(exc))
        return
    finally:
        lock.release()

    queue.put("acquired")


@pytest.mark.skipif(sys.platform == "win32", reason="Lock semantics differ on Windows")
def test_single_instance_lock_blocks_second_process(tmp_path):
    """A second Janus server process should fail to acquire the same runtime lock."""
    lock_path = tmp_path / "janus.lock"
    first_lock = runtime.SingleInstanceLock(lock_path)
    first_lock.acquire()

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_acquire_lock, args=(str(lock_path), queue))
    process.start()

    try:
        process.join(timeout=5)
        assert not process.is_alive(), "child process did not exit within timeout"
        assert process.exitcode == 0
        result = queue.get(timeout=1)
        assert "single-instance" in result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        first_lock.release()


def test_single_instance_lock_closes_fd_when_pid_write_fails(monkeypatch, tmp_path):
    """Lock acquisition should close the file descriptor if PID metadata cannot be persisted."""
    lock_path = tmp_path / "janus.lock"
    close_mock = MagicMock()
    fcntl_stub = ModuleType("fcntl")
    fcntl_stub.LOCK_EX = 1
    fcntl_stub.LOCK_NB = 2
    fcntl_stub.LOCK_UN = 8
    fcntl_stub.flock = MagicMock()

    monkeypatch.setattr(runtime, "prepare_runtime_paths", lambda: None)
    monkeypatch.setattr(runtime.os, "open", lambda *args, **kwargs: 42)
    monkeypatch.setattr(runtime.os, "ftruncate", MagicMock(side_effect=OSError("disk full")))
    monkeypatch.setattr(runtime.os, "close", close_mock)
    monkeypatch.setitem(sys.modules, "fcntl", fcntl_stub)

    lock = runtime.SingleInstanceLock(lock_path)

    with pytest.raises(RuntimeError, match="Failed to update Janus runtime lock file"):
        lock.acquire()

    fcntl_stub.flock.assert_called_once_with(42, fcntl_stub.LOCK_EX | fcntl_stub.LOCK_NB)
    close_mock.assert_called_once_with(42)
    assert lock._fd is None


def test_email_notification_filters_block_and_allow():
    """Immediate notifications should honor sender and keyword filters."""
    main = importlib.import_module("janus.main")
    email = models.EmailRecord(
        sender="promo@example.com",
        subject="Weekly newsletter",
        snippet="digest",
        priority=models.Priority.IMPORTANT,
    )

    preference = models.NotificationPreference(
        user_id=1,
        notify_important=True,
        sender_blocklist="promo@example.com",
    )
    assert main._should_notify_important_email(email, preference) is False

    preference = models.NotificationPreference(
        user_id=1,
        notify_important=True,
        sender_allowlist="vip@example.com",
    )
    assert main._should_notify_important_email(email, preference) is False

    preference = models.NotificationPreference(
        user_id=1,
        notify_important=True,
        keyword_allowlist="newsletter",
    )
    assert main._should_notify_important_email(email, preference) is True


def test_build_digest_generation_payloads_handles_missing_user():
    """Digest payload generation should fall back cleanly when no topic-aware user is available."""
    main = importlib.import_module("janus.main")
    emails = [
        models.EmailRecord(
            sender="sender@example.com",
            subject="Digest subject",
            snippet="Digest body",
        )
    ]

    payloads, topic_ids = main._build_digest_generation_payloads(MagicMock(), emails, user=None)

    assert payloads == [
        {
            "sender": "sender@example.com",
            "subject": "Digest subject",
            "snippet": "Digest body",
        }
    ]
    assert topic_ids == []


def test_build_digest_generation_payloads_excludes_candidate_topics():
    """Topics still awaiting corroboration must not group digest mail or be marked as digested."""
    main = importlib.import_module("janus.main")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        user = models.User(username="owner", hashed_password="hash", is_admin=True)
        db.add_all([user, models.Settings()])
        db.commit()
        db.refresh(user)
        account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
        db.add(account)
        db.commit()
        db.refresh(account)
        email = models.EmailRecord(
            account_id=account.id,
            message_id="digest-1",
            sender="sender@example.com",
            subject="Digest subject",
            snippet="Digest body",
            priority=models.Priority.DIGEST,
            received_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
        )
        active_topic = models.TrackedTopic(
            user_id=user.id,
            slug="active-topic",
            title="Active topic",
            status=models.TopicStatus.ACTIVE,
            origin=models.TopicOrigin.AUTO,
        )
        candidate_topic = models.TrackedTopic(
            user_id=user.id,
            slug="candidate-topic",
            title="Candidate topic",
            status=models.TopicStatus.CANDIDATE,
            origin=models.TopicOrigin.AUTO,
        )
        db.add_all([email, active_topic, candidate_topic])
        db.commit()
        db.add_all(
            [
                models.TopicEvidence(
                    topic_id=topic.id,
                    source_type=models.TopicEvidenceSource.EMAIL,
                    source_id=email.id,
                    evidence_role=models.TopicEvidenceRole.TRIGGER,
                )
                for topic in (active_topic, candidate_topic)
            ]
        )
        db.commit()

        payloads, topic_ids = main._build_digest_generation_payloads(db, [email], user=user)

        assert topic_ids == [active_topic.id]
        assert [topic["title"] for topic in payloads[0]["topics"]] == ["Active topic"]
    finally:
        db.close()
        engine.dispose()


def test_brief_reports_missing_default_user(monkeypatch, capsys):
    """CLI briefing should fail closed when no default user is configured."""
    main = importlib.import_module("janus.main")

    class FakePlannerService:
        def __init__(self):
            self.db = MagicMock()

    monkeypatch.setattr(main.database, "ensure_schema_up_to_date", lambda: None)
    monkeypatch.setattr(sys.modules["janus"], "planner_service", SimpleNamespace(PlannerService=FakePlannerService))
    monkeypatch.setattr(main.TopicService, "get_default_user", lambda self: None)

    main.brief()

    assert "No default user found. Please bootstrap an admin user first." in capsys.readouterr().out


def test_digest_runs_the_default_users_builtin_digest_definition(db, monkeypatch, capsys):
    """The CLI digest runs the default user's built-in digest trigger inline and prints its output.

    Email selection (user scoping and the result cap) now lives in the
    built-in digest's prepare hook and is covered with the builtin triggers.
    """
    from janus import builtin_triggers
    from janus import trigger_definition_service as definitions

    main = importlib.import_module("janus.main")
    default_user = models.User(username="default-user", hashed_password="hash", is_admin=True)
    other_user = models.User(username="other-user", hashed_password="hash", is_admin=False)
    db.add_all([default_user, other_user])
    db.commit()
    builtin_triggers.ensure_builtin_definitions(db, other_user.id)
    default_user_id, other_user_id = default_user.id, other_user.id

    monkeypatch.setattr(main.database, "ensure_schema_up_to_date", lambda: None)
    monkeypatch.setattr(main.database, "SessionLocal", lambda: db)
    ran: list[int] = []

    def fake_run_definition_inline(session_factory, definition_id, **_kwargs):
        ran.append(definition_id)
        return SimpleNamespace(
            status=models.RuntimeTriggerStatus.COMPLETED.value,
            output="digest summary",
            last_error=None,
        )

    monkeypatch.setattr("janus.trigger_runtime.run_definition_inline", fake_run_definition_inline)

    main.digest()

    default_definition = definitions.get_builtin_definition(db, default_user_id, "builtin.digest")
    other_definition = definitions.get_builtin_definition(db, other_user_id, "builtin.digest")
    assert default_definition is not None
    assert ran == [default_definition.id]
    assert ran != [other_definition.id]
    out = capsys.readouterr().out
    assert "Afternoon Digest" in out
    assert "digest summary" in out


def test_digest_reports_a_failed_run(db, monkeypatch, capsys):
    """A failed built-in digest run is reported instead of printing an empty digest."""
    main = importlib.import_module("janus.main")
    db.add(models.User(username="default-user", hashed_password="hash", is_admin=True))
    db.commit()

    monkeypatch.setattr(main.database, "ensure_schema_up_to_date", lambda: None)
    monkeypatch.setattr(main.database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        "janus.trigger_runtime.run_definition_inline",
        lambda *_args, **_kwargs: SimpleNamespace(status="failed", output=None, last_error="provider down"),
    )

    main.digest()

    out = capsys.readouterr().out
    assert "The run failed: provider down" in out
    assert "Afternoon Digest" not in out


def test_app_factory_blocking_routes_remain_sync():
    """Sync routes keep blocking DB/filesystem work off the main event loop."""
    main = importlib.import_module("janus.main")
    events_api = importlib.import_module("janus.api.events")

    assert not inspect.iscoroutinefunction(events_api.list_events)
    assert not inspect.iscoroutinefunction(events_api.list_event_sources)
    assert not inspect.iscoroutinefunction(main.serve_spa)


def test_email_callback_does_not_renotify_already_notified_emails(db, testing_session_local, monkeypatch):
    """Re-processing an already-notified IMPORTANT email must not fire a second notification."""
    main = importlib.import_module("janus.main")

    user = models.User(username="admin", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(
        name="Inbox",
        email="inbox@example.com",
        username="inbox@example.com",
        password="secret",
        user_id=user.id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    existing = models.EmailRecord(
        account_id=account.id,
        message_id="<already-notified@example.com>",
        subject="Already notified",
        sender="important@example.com",
        snippet="already done",
        priority=models.Priority.IMPORTANT,
        notified=True,
    )
    db.add(existing)
    db.commit()
    db.close()

    notify_mock = MagicMock()
    monkeypatch.setattr(main.notifications.NotificationService, "notify", notify_mock)
    monkeypatch.setattr(main, "database", MagicMock(SessionLocal=testing_session_local))

    email_data = {
        "uid": 999,
        "message_id": "<already-notified@example.com>",
        "sender": "important@example.com",
        "subject": "Already notified",
        "snippet": "already done",
        "received_at": datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
    }
    main.email_callback(email_data)
    notify_mock.assert_not_called()


def _run_config_doctor(monkeypatch, tmp_path, **overrides):
    """Invoke ``janus config doctor`` with patched settings."""
    main = importlib.import_module("janus.main")
    key_path = tmp_path / ".janus_key"
    key_path.write_text("key", encoding="utf-8")

    settings = SimpleNamespace(
        is_placeholder_key=False,
        base_url="http://localhost:8080",
        port=8080,
        host="127.0.0.1",
        allowed_origins=["http://localhost:8080"],
        allowed_hosts=["localhost", "127.0.0.1"],
        fernet_key_path=key_path,
        server_mode=False,
        database_url=f"sqlite:///{tmp_path / 'janus.db'}",
    )
    for field, value in overrides.items():
        setattr(settings, field, value)

    monkeypatch.setattr(main, "settings", settings)
    return CliRunner().invoke(main.app, ["config", "doctor"])


def test_config_doctor_clean(monkeypatch, tmp_path):
    """Valid config should pass without warnings or errors."""
    result = _run_config_doctor(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert "✅ No configuration issues found." in result.output


def test_config_doctor_placeholder_key(monkeypatch, tmp_path):
    """Placeholder secret keys should fail the doctor check."""
    result = _run_config_doctor(monkeypatch, tmp_path, is_placeholder_key=True)

    assert result.exit_code == 1, result.output
    assert "secret_key is still the default placeholder" in result.output


@pytest.mark.parametrize("installed", [True, False])
def test_config_doctor_postgres_driver(monkeypatch, tmp_path, installed):
    """A PostgreSQL database_url without the postgres extra installed is an error."""
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: (object() if installed else None) if name == "psycopg2" else real_find_spec(name, *a),
    )
    result = _run_config_doctor(monkeypatch, tmp_path, database_url="postgresql://janus@localhost/janus")

    assert ("the driver is not installed" in result.output) is not installed, result.output
    assert result.exit_code == (0 if installed else 1), result.output


def test_config_doctor_port_mismatch(monkeypatch, tmp_path):
    """Mismatched ``port`` and ``base_url`` ports should be flagged."""
    result = _run_config_doctor(
        monkeypatch,
        tmp_path,
        base_url="http://localhost:9090",
        port=8080,
        allowed_origins=["http://localhost:9090", "http://localhost:8080"],
    )

    assert result.exit_code == 1, result.output
    assert "port (8080) does not match the port in base_url (http://localhost:9090)" in result.output


def test_config_doctor_reverse_proxy_public_base_url(monkeypatch, tmp_path):
    """Reverse-proxied public base URLs should not be rejected just because the bind port differs."""
    result = _run_config_doctor(
        monkeypatch,
        tmp_path,
        base_url="https://janus.example.com",
        port=8102,
        host="0.0.0.0",
        allowed_origins=["https://janus.example.com"],
        allowed_hosts=["janus.example.com"],
    )

    assert result.exit_code == 0, result.output
    assert "The bind-address origin (https://localhost:8102) is not in allowed_origins" in result.output


def test_config_doctor_origin_not_in_allowed_origins(monkeypatch, tmp_path):
    """The public origin from ``base_url`` should drive the allowed_origins error."""
    result = _run_config_doctor(
        monkeypatch,
        tmp_path,
        base_url="https://myapp.example.com",
        port=443,
        host="0.0.0.0",
        allowed_origins=["https://localhost"],
        allowed_hosts=["myapp.example.com"],
    )

    assert result.exit_code == 1, result.output
    assert "The public server origin (https://myapp.example.com) derived from base_url" in result.output


def test_config_doctor_http_server_mode(monkeypatch, tmp_path):
    """Server mode over HTTP on a public hostname should emit a warning."""
    result = _run_config_doctor(
        monkeypatch,
        tmp_path,
        base_url="http://example.com",
        port=80,
        host="example.com",
        allowed_origins=["http://example.com"],
        allowed_hosts=["example.com"],
        server_mode=True,
    )

    assert result.exit_code == 0, result.output
    assert "base_url uses plain HTTP in server_mode for a non-localhost host" in result.output

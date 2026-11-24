"""Tests for user-level configuration file support."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from janus import runtime
from janus.config import Settings, get_config_dir, get_config_file_path, mask_database_url

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_settings(toml_path: Path, env: dict | None = None) -> Settings:
    """Instantiate Settings with a specific TOML file path and optional env overrides.

    Uses monkeypatching on the class-level ``_toml_config_file`` to redirect
    the TOML source without touching the real user home directory.
    """
    env = env or {}
    # Provide a valid secret_key so the model_validator doesn't raise.
    env.setdefault("JANUS_SECRET_KEY", "test-secret-key-for-unit-tests")

    with patch.object(Settings, "_toml_config_file", toml_path):
        return Settings(
            _env_file=None,  # ignore .env during tests
        )


# ---------------------------------------------------------------------------
# get_config_file_path
# ---------------------------------------------------------------------------


def test_config_file_path_default(monkeypatch):
    """Falls back to ~/.config/janus/settings.toml when XDG_CONFIG_HOME is unset."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = get_config_file_path()
    assert path == Path.home() / ".config" / "janus" / "settings.toml"


def test_config_dir_default(monkeypatch):
    """Falls back to ~/.config/janus when XDG_CONFIG_HOME is unset."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert get_config_dir() == Path.home() / ".config" / "janus"


def test_config_file_path_xdg(monkeypatch, tmp_path):
    """Honours $XDG_CONFIG_HOME when it is set."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = get_config_file_path()
    assert path == tmp_path / "janus" / "settings.toml"


def test_config_dir_xdg(monkeypatch, tmp_path):
    """Honours $XDG_CONFIG_HOME for the shared Janus config directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert get_config_dir() == tmp_path / "janus"


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------


def test_toml_loaded_correctly(tmp_path, monkeypatch):
    """Values from the TOML file are merged into Settings."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text(
        'secret_key = "toml-secret"\ndatabase_url = "sqlite:///toml.db"\nport = 9999\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None)
    assert s.janus_secret_key == "toml-secret"
    assert s.database_url == "sqlite:///toml.db"
    assert s.port == 9999


def test_missing_toml_file_does_not_raise(tmp_path, monkeypatch):
    """Janus starts normally when the TOML file is absent."""
    missing = tmp_path / "nonexistent.toml"
    monkeypatch.setenv("JANUS_SECRET_KEY", "env-secret")
    with patch.object(Settings, "_toml_config_file", missing):
        s = Settings(_env_file=None)
    assert s.janus_secret_key == "env-secret"


def test_env_overrides_toml(tmp_path, monkeypatch):
    """Environment variables take precedence over TOML values."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text('port = 1111\nsecret_key = "toml-secret"\n', encoding="utf-8")
    monkeypatch.setenv("JANUS_SECRET_KEY", "env-wins")
    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None)
    # env should win over TOML
    assert s.janus_secret_key == "env-wins"


def test_allowed_hosts_supports_comma_separated_env_values(tmp_path, monkeypatch):
    """Host allowlists from the environment should parse as a list."""
    monkeypatch.setenv("JANUS_SECRET_KEY", "env-secret")
    monkeypatch.setenv("JANUS_ALLOWED_HOSTS", "janus.example.com, api.example.com:8443")

    with patch.object(Settings, "_toml_config_file", tmp_path / "missing.toml"):
        s = Settings(_env_file=None)

    assert s.allowed_hosts == ["janus.example.com", "api.example.com:8443"]


def test_allowed_origins_none_normalizes_to_empty_list(tmp_path, monkeypatch):
    """A null origin allowlist source should fail closed without crashing validation."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text('secret_key = "toml-secret"\nallowed_origins = []\n', encoding="utf-8")
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)

    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None, allowed_origins=None)

    assert s.allowed_origins == []


def test_allowed_origins_reject_non_iterable_values(tmp_path, monkeypatch):
    """Non-iterable allowlist misconfiguration should raise a clear validation error."""
    monkeypatch.setenv("JANUS_SECRET_KEY", "env-secret")

    with patch.object(Settings, "_toml_config_file", tmp_path / "missing.toml"):  # noqa: SIM117
        with pytest.raises(ValueError, match="allowed_origins must be a comma-separated string or iterable of origins"):
            Settings(_env_file=None, allowed_origins=123)


def test_invalid_allowed_host_is_rejected(tmp_path, monkeypatch):
    """Host allowlist entries should fail fast when malformed."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text('secret_key = "toml-secret"\nallowed_hosts = ["bad host"]\n', encoding="utf-8")
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)

    with patch.object(Settings, "_toml_config_file", toml_file):  # noqa: SIM117
        with pytest.raises(ValueError, match="Invalid host allowlist entry"):
            Settings(_env_file=None)


def test_default_runtime_paths_are_absolute(tmp_path, monkeypatch):
    """Default runtime paths should use persistent absolute locations."""
    monkeypatch.setenv("JANUS_SECRET_KEY", "env-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(Settings, "_toml_config_file", tmp_path / "missing.toml"):
        s = Settings(_env_file=None)

    database_path = runtime.sqlite_database_path(s.database_url)
    assert database_path is not None
    assert database_path.is_absolute()
    assert s.fernet_key_path.is_absolute()
    assert s.log_file_path.is_absolute()


def test_relative_runtime_paths_are_rejected(tmp_path, monkeypatch):
    """Explicit runtime paths must be absolute so deployments stay portable."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text('secret_key = "toml-secret"\nfernet_key_path = "relative.key"\n', encoding="utf-8")
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)

    with patch.object(Settings, "_toml_config_file", toml_file):  # noqa: SIM117
        with pytest.raises(ValueError, match="absolute persistent paths"):
            Settings(_env_file=None)


# ---------------------------------------------------------------------------
# mask_database_url
# ---------------------------------------------------------------------------


def test_mask_database_url_with_credentials():
    """Passwords in database URLs are replaced with ***."""
    url = "postgresql://user:secret@localhost:5432/db"
    assert mask_database_url(url) == "postgresql://user:***@localhost:5432/db"


def test_mask_database_url_without_credentials():
    """Database URLs without credentials pass through unchanged."""
    url = "sqlite:///./janus.db"
    assert mask_database_url(url) == "sqlite:///./janus.db"


# ---------------------------------------------------------------------------
# Settings.show()
# ---------------------------------------------------------------------------


def test_show_masks_secret_key(tmp_path, monkeypatch):
    """settings.show() always masks the secret_key field."""
    monkeypatch.setenv("JANUS_SECRET_KEY", "super-secret")
    missing = tmp_path / "nonexistent.toml"
    with patch.object(Settings, "_toml_config_file", missing):
        s = Settings(_env_file=None)
    shown = s.show()
    assert shown["secret_key"] == "***"


def test_show_masks_db_password(tmp_path, monkeypatch):
    """settings.show() masks passwords in the database_url."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text(
        'secret_key = "s"\ndatabase_url = "postgresql://user:pass@localhost:5432/db"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None)
    shown = s.show()
    assert "pass" not in shown["database_url"]
    assert "***" in shown["database_url"]


def test_show_returns_host_and_port(tmp_path, monkeypatch):
    """settings.show() includes host and port."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text(
        'secret_key = "s"\nhost = "0.0.0.0"\nport = 8080\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None)
    shown = s.show()
    assert shown["host"] == "0.0.0.0"
    assert shown["port"] == 8080


def test_show_includes_public_surface_hardening_settings(tmp_path, monkeypatch):
    """settings.show() should surface the hardening-related toggles and allowlist."""
    toml_file = tmp_path / "settings.toml"
    toml_file.write_text(
        "\n".join(
            [
                'secret_key = "s"',
                'allowed_hosts = ["janus.example.com"]',
                "public_api_docs_enabled = true",
                "public_setup_enabled = false",
                "runtime_trigger_worker_enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("JANUS_SECRET_KEY", raising=False)

    with patch.object(Settings, "_toml_config_file", toml_file):
        s = Settings(_env_file=None)

    shown = s.show()
    assert shown["allowed_hosts"] == ["janus.example.com"]
    assert shown["public_api_docs_enabled"] is True
    assert shown["public_setup_enabled"] is False
    assert shown["runtime_trigger_worker_enabled"] is True


# ---------------------------------------------------------------------------
# janus config init / show CLI commands
# ---------------------------------------------------------------------------


def test_config_init_creates_file(tmp_path, monkeypatch):
    """``janus config init`` writes a template file when none exists."""
    from typer.testing import CliRunner

    import janus.main as main_module
    from janus.main import app

    config_file = tmp_path / "janus" / "settings.toml"
    monkeypatch.setattr(main_module, "get_config_file_path", lambda: config_file)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert config_file.exists()
    content = config_file.read_text()
    assert "secret_key" in content


def test_config_init_noop_if_exists(tmp_path, monkeypatch):
    """``janus config init`` prints a warning and does not overwrite an existing file."""
    from typer.testing import CliRunner

    import janus.main as main_module
    from janus.main import app

    config_file = tmp_path / "janus" / "settings.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("# existing\n", encoding="utf-8")
    monkeypatch.setattr(main_module, "get_config_file_path", lambda: config_file)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0
    # File should remain unchanged
    assert config_file.read_text() == "# existing\n"
    assert "already exists" in result.output


def test_config_show_masks_values(monkeypatch):
    """``janus config show`` prints effective config with secrets masked."""
    from typer.testing import CliRunner

    from janus.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "***" in result.output
    assert "port" in result.output
    assert "host" in result.output


def test_config_doctor_treats_unspecified_ipv6_bind_host_as_localhost(tmp_path, monkeypatch):
    """IPv6 wildcard bind hosts should map to localhost for origin validation."""
    from typer.testing import CliRunner

    import janus.main as main_module
    from janus.main import app

    key_path = tmp_path / "janus.key"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            is_placeholder_key=False,
            base_url="http://localhost:8080",
            port=8080,
            host="::",
            allowed_origins=["http://localhost:8080"],
            allowed_hosts=["localhost"],
            fernet_key_path=key_path,
            server_mode=False,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "doctor"])

    assert result.exit_code == 0, result.output
    assert "No configuration issues found" in result.output


def test_config_doctor_handles_ipv6_loopback_hosts_consistently(tmp_path, monkeypatch):
    """IPv6 loopback base URLs should be treated like localhost across all checks."""
    from typer.testing import CliRunner

    import janus.main as main_module
    from janus.main import app

    key_path = tmp_path / "janus.key"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            is_placeholder_key=False,
            base_url="http://[::1]:8080",
            port=8080,
            host="::1",
            allowed_origins=["http://localhost:8080"],
            allowed_hosts=["[::1]:8080"],
            fernet_key_path=key_path,
            server_mode=True,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "doctor"])

    assert result.exit_code == 0, result.output
    assert "server_mode is enabled but base_url still points to localhost" in result.output
    assert "hostname from base_url" not in result.output
    assert "plain HTTP in server_mode for a non-localhost host" not in result.output

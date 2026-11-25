"""`janus service`: the systemd user unit, its commands, and the restart after `janus update` (JAN-399)."""

import shutil
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from janus import cli_update
from janus.cli import app
from janus.services import systemd_unit, update_service


@pytest.fixture
def systemd(tmp_path, monkeypatch):
    """A fake systemctl that records its calls, and a private XDG config home."""
    calls: list[tuple[str, ...]] = []
    state = {"active": True}

    def fake_run(command, **kwargs):
        args = tuple(command[2:])  # drop "systemctl --user"
        calls.append(args)
        returncode = 0 if args[0] != "is-active" or state["active"] else 3
        return subprocess.CompletedProcess(command, returncode, stdout="active\n", stderr="")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(systemd_unit, "_systemctl", lambda: "systemctl")
    monkeypatch.setattr(systemd_unit.subprocess, "run", fake_run)
    monkeypatch.setattr(systemd_unit, "janus_executable", lambda: "/opt/my tools/bin/janus")
    return calls, state


def test_render_unit_quotes_paths_and_runs_janus_start(tmp_path):
    """ExecStart runs `janus start`; paths with spaces, $ and % are quoted for systemd."""
    unit = systemd_unit.render_unit("/opt/my tools/bin/janus", tmp_path / "100%", "/a$b:/usr/bin")
    assert 'ExecStart="/opt/my tools/bin/janus" start' in unit
    assert f'WorkingDirectory="{tmp_path}/100%%"' in unit
    assert 'Environment="PATH=/a$$b:/usr/bin"' in unit
    assert "WantedBy=default.target" in unit


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_rendered_unit_passes_systemd_analyze(tmp_path):
    """Systemd itself accepts the unit (checked without installing it)."""
    (tmp_path / "with space").mkdir()
    executable = tmp_path / "with space" / "janus"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    unit = tmp_path / "janus.service"
    unit.write_text(systemd_unit.render_unit(str(executable), tmp_path, "/usr/bin:/bin"))
    result = subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "janus.service" not in result.stderr, result.stderr


def test_install_writes_the_unit_and_enables_it(systemd, tmp_path, monkeypatch):
    """Install writes the unit, reloads systemd and enables it with --now."""
    calls, _ = systemd
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["service", "install"])

    assert result.exit_code == 0, result.output
    unit = (tmp_path / "config" / "systemd" / "user" / "janus.service").read_text()
    assert 'ExecStart="/opt/my tools/bin/janus" start' in unit
    assert f"WorkingDirectory={tmp_path}" in unit
    assert calls == [("daemon-reload",), ("enable", "--now", "janus.service")]
    assert "loginctl enable-linger" in result.output


def test_install_without_start_and_uninstall(systemd):
    """--no-start only enables; uninstall disables, removes the unit and reloads."""
    calls, _ = systemd
    runner = CliRunner()
    assert runner.invoke(app, ["service", "install", "--no-start"]).exit_code == 0
    assert calls[-1] == ("enable", "janus.service")

    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0 and "Removed" in result.output
    assert calls[-2:] == [("disable", "--now", "janus.service"), ("daemon-reload",)]
    assert not systemd_unit.is_installed()
    assert "No Janus service" in runner.invoke(app, ["service", "uninstall"]).output


def test_without_systemd_the_commands_explain_why(monkeypatch, tmp_path):
    """No systemctl: a clear error, and is_active() is simply False."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(systemd_unit.shutil, "which", lambda name: None)
    result = CliRunner().invoke(app, ["service", "install"])
    assert result.exit_code == 1
    assert "needs systemd" in result.output
    assert systemd_unit.is_active() is False


def test_janus_executable_prefers_the_running_command(tmp_path, monkeypatch):
    """The janus being run is the one the service starts; else the one on the PATH."""
    running = tmp_path / "janus"
    running.write_text("")
    running.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(running), "service", "install"])
    assert systemd_unit.janus_executable() == str(running.resolve())

    monkeypatch.setattr(sys, "argv", ["python", "-m", "janus.main"])
    monkeypatch.setattr(systemd_unit.shutil, "which", lambda name: None)
    with pytest.raises(systemd_unit.ServiceError):
        systemd_unit.janus_executable()


@pytest.mark.parametrize("active", [True, False])
def test_update_restarts_an_active_service(systemd, monkeypatch, active):
    """After an install, `janus update` restarts the service when it is running."""
    calls, state = systemd
    state["active"] = active
    systemd_unit.install(start=True)
    release = update_service.Release("v9.0.0", (9, 0, 0), None, None)
    monkeypatch.setattr(update_service, "check_for_update", lambda: release)
    monkeypatch.setattr(update_service, "installed_version", lambda: (1, 0, 0))
    monkeypatch.setattr(update_service, "apply_update", lambda r: None)
    monkeypatch.setattr(
        cli_update.update_service, "eligibility", lambda: update_service.Eligibility(True, "eligible", kind="uv tool")
    )

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert (("restart", "janus.service") in calls) is active
    assert ("restarted the janus service" in result.output) is active

"""Run Janus as a systemd user service (``janus service``, JAN-399).

``install`` writes ``~/.config/systemd/user/janus.service`` (``$XDG_CONFIG_HOME`` respected) for
``janus start``, then enables and starts it, so Janus runs in the background and starts at login.
The unit keeps the installing shell's ``PATH`` (so the auto-update finds ``uv``/``pipx``), its XDG
config/data/state overrides (so the service uses the same settings, database and key file), and
its working directory (so a ``.env`` there is still read). Other variables, secrets included, are
never written into the unit. ``loginctl enable-linger`` starts it at boot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "janus.service"
#: Variables copied from the installing shell into the unit when set.
KEPT_ENVIRONMENT = ("PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
_TIMEOUT_SECONDS = 60


class ServiceError(Exception):
    """systemd is unavailable, or a systemctl command failed."""


def unit_path() -> Path:
    """Where the user unit lives."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "systemd" / "user" / UNIT_NAME


def janus_executable() -> str:
    """The ``janus`` entry point to run: the one running now, else the one on the PATH."""
    current = Path(sys.argv[0])
    if current.name == "janus" and current.is_file() and os.access(current, os.X_OK):
        return str(current.resolve())
    found = shutil.which("janus")
    if found is None:
        raise ServiceError("cannot find the janus command; run this from the janus you want to start")
    return found


def _quote(arg: str) -> str:
    """Quote one value for systemd (spaces, quotes, backslashes, ``%`` specifiers).

    ``$`` stays as it is: systemd expands variables only in ExecStart's arguments, not in its
    executable path, ``WorkingDirectory`` or ``Environment``, and the only argument is ``start``.
    """
    if arg and not any(ch in arg for ch in " \"\\'%"):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def render_unit(executable: str, working_dir: Path, environment: dict[str, str]) -> str:
    """The unit file text for ``janus start``."""
    env_lines = "".join(f"Environment={_quote(f'{name}={value}')}\n" for name, value in sorted(environment.items()))
    return f"""[Unit]
Description=Janus personal AI agent
Documentation=https://github.com/thomasniebler/janus
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={_quote(executable)} start
WorkingDirectory={str(working_dir).replace("%", "%%")}
{env_lines}Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _systemctl() -> str:
    found = shutil.which("systemctl")
    if found is None or not sys.platform.startswith("linux"):
        raise ServiceError("janus service needs systemd (Linux); start Janus with `janus start` instead")
    return found


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user <args>``."""
    try:
        result = subprocess.run(
            [_systemctl(), "--user", *args], capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(f"systemctl --user {' '.join(args)} failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ServiceError(f"systemctl --user {' '.join(args)} failed: {detail}")
    return result


def install(*, start: bool = True) -> Path:
    """Write the unit, reload systemd, and enable (and start) the service."""
    _systemctl()
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    environment = {name: os.environ[name] for name in KEPT_ENVIRONMENT if os.environ.get(name)}
    path.write_text(render_unit(janus_executable(), Path.cwd(), environment))
    systemctl("daemon-reload")
    systemctl("enable", *(["--now"] if start else []), UNIT_NAME)
    return path


def uninstall() -> bool:
    """Stop and disable the service and remove the unit; False when none was installed."""
    path = unit_path()
    if not path.exists():
        return False
    systemctl("disable", "--now", UNIT_NAME, check=False)
    path.unlink()
    systemctl("daemon-reload")
    return True


def is_installed() -> bool:
    """Whether the unit file exists."""
    return unit_path().exists()


def is_active() -> bool:
    """Whether the service is installed and running; False without systemd."""
    if not is_installed():
        return False
    try:
        return systemctl("is-active", "--quiet", UNIT_NAME, check=False).returncode == 0
    except ServiceError:
        return False


def restart() -> None:
    """Restart the running service (``janus update`` uses this)."""
    systemctl("restart", UNIT_NAME)

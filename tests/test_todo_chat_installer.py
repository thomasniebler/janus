"""Regression tests for the Todo Chat installer script."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SOURCE = REPO_ROOT / "scripts" / "install-todo-chat.sh"
SERVICE_SOURCE = REPO_ROOT / "modules" / "todo-chat" / "packaging" / "todo-chat.service"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _build_temp_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    installer_target = repo_root / "scripts" / "install-todo-chat.sh"
    installer_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INSTALLER_SOURCE, installer_target)
    service_target = repo_root / "modules" / "todo-chat" / "packaging" / SERVICE_SOURCE.name
    service_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SERVICE_SOURCE, service_target)

    tauri_bin = repo_root / "modules" / "todo-chat" / "frontend" / "node_modules" / ".bin" / "tauri"
    tauri_bin.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(
        tauri_bin,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "mkdir -p src-tauri/target/release\n"
        "printf '#!/usr/bin/env bash\\n' > src-tauri/target/release/todo-chat\n"
        "chmod +x src-tauri/target/release/todo-chat\n",
    )
    return repo_root


def _build_fake_commands(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(bin_dir / "npm", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        bin_dir / "systemctl",
        '#!/usr/bin/env bash\nif [[ "${1:-}" == "--user" ]]; then\n  shift\nfi\nexit 0\n',
    )
    _write_executable(
        bin_dir / "hyprctl",
        '#!/usr/bin/env bash\nif [[ "${1:-}" == "instances" ]]; then\n  printf \'[]\\n\'\n  exit 0\nfi\nexit 0\n',
    )
    _write_executable(
        bin_dir / "install",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'args=("$@")\n'
        "src=${args[$(($#-2))]}\n"
        "dst=${args[$(($#-1))]}\n"
        'mkdir -p "$(dirname "$dst")"\n'
        'cp "$src" "$dst"\n',
    )
    return bin_dir


def _run_installer(repo_root: Path, home: Path, config_root: Path, *, set_xdg: bool) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{_build_fake_commands(home).as_posix()}:{env['PATH']}"
    if set_xdg:
        env["XDG_CONFIG_HOME"] = str(config_root)
    else:
        env.pop("XDG_CONFIG_HOME", None)
    subprocess.run(
        ["/usr/bin/env", "bash", str(repo_root / "scripts" / "install-todo-chat.sh")],
        check=True,
        cwd=repo_root,
        env=env,
    )


def test_installer_migrates_legacy_hyprland_entries(tmp_path: Path) -> None:
    """Legacy main-config Todo Chat entries are replaced by one managed source/bind."""
    repo_root = _build_temp_repo(tmp_path)
    home = tmp_path / "home"
    config_root = home / ".config"
    hypr_root = config_root / "hypr"
    (hypr_root / "conf.d").mkdir(parents=True)
    hypr_config = hypr_root / "hyprland.conf"
    hypr_config.write_text(
        "\n".join(
            [
                "# keep me",
                "monitor = DP-1, preferred, auto, 1",
                "bind = SUPER, T, exec, /home/test/.local/bin/todo-chat --toggle",
                "windowrule = match:class ^(todo-chat)$, float on",
                "exec-once = systemctl --user start todo-chat.service",
                "",
            ]
        )
    )
    (hypr_root / "conf.d" / "keybinds.conf").write_text("bind = SUPER, Q, exec, kitty\n")
    (hypr_root / "conf.d" / "rules.conf").write_text("windowrule = opacity 0.9 override, class:(kitty)\n")
    (hypr_root / "conf.d" / "autostart.conf").write_text("exec-once = nm-applet\n")

    _run_installer(repo_root, home, config_root, set_xdg=False)

    companion_rules = hypr_root / "conf.d" / "janus-companion.conf"
    assert companion_rules.exists()
    assert "todo-chat --toggle" not in hypr_config.read_text()
    assert "todo-chat.service" not in hypr_config.read_text()
    assert "monitor = DP-1, preferred, auto, 1" in hypr_config.read_text()
    assert hypr_config.read_text().count("source = ") == 1
    assert hypr_config.read_text().count("SUPER, T") == 0
    assert companion_rules.read_text().count("SUPER, T") == 1


def test_installer_uses_custom_xdg_source_line_idempotently(tmp_path: Path) -> None:
    """The managed Hyprland source follows XDG_CONFIG_HOME and is added only once."""
    repo_root = _build_temp_repo(tmp_path)
    home = tmp_path / "home"
    config_root = tmp_path / "xdg-config"
    hypr_root = config_root / "hypr"
    (hypr_root / "conf.d").mkdir(parents=True)
    hypr_config = hypr_root / "hyprland.conf"
    hypr_config.write_text("# managed elsewhere\n")

    _run_installer(repo_root, home, config_root, set_xdg=True)
    _run_installer(repo_root, home, config_root, set_xdg=True)

    expected_source = f"source = {hypr_root / 'conf.d' / 'janus-companion.conf'}"
    hypr_text = hypr_config.read_text()
    assert expected_source in hypr_text
    assert "~/.config/hypr/conf.d/janus-companion.conf" not in hypr_text
    assert hypr_text.count(expected_source) == 1

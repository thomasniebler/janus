"""Auto-update of a `uv tool` / `pipx` install of the release wheel (JAN-398)."""

import hashlib
import sqlite3
import sys
import threading

import httpx
import pytest

from janus import config, resources
from janus.services import update_boot, update_service, update_tool_install

WHEEL_NEW = b"new wheel"
WHEEL_OLD = b"old wheel"


def _release_payload(tag: str, wheel: bytes) -> dict:
    version = tag.removeprefix("v")
    name = f"janus_assistant-{version}-py3-none-any.whl"
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.invalid/{tag}/{name}",
                "digest": "sha256:" + hashlib.sha256(wheel).hexdigest(),
            }
        ],
    }


def _client() -> httpx.Client:
    """GitHub with v1.0.0 (installed) and v1.1.0 (latest), each carrying a wheel."""
    releases = {"v1.0.0": WHEEL_OLD, "v1.1.0": WHEEL_NEW}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/releases/latest"):
            return httpx.Response(200, json=_release_payload("v1.1.0", WHEEL_NEW))
        if "/releases/tags/" in path:
            tag = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=_release_payload(tag, releases[tag]))
        tag = path.split("/")[1]
        return httpx.Response(200, content=releases[tag])

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def tool_install(tmp_path, monkeypatch):
    """A uv tool environment at version 1.0.0 with an SQLite database and its own state dir."""
    env = tmp_path / "tools" / "janus"
    env.mkdir(parents=True)
    (env / "uv-receipt.toml").write_text("[tool]\n")
    site = tmp_path / "site-packages"  # stands in for REPO_ROOT: no .git, no pyproject.toml
    site.mkdir()
    db = tmp_path / "janus.db"
    sqlite3.connect(db).close()

    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(config.settings, "auto_update_enabled", True)
    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{db}")
    monkeypatch.setattr(update_service.importlib.metadata, "version", lambda name: "1.0.0")
    monkeypatch.setattr(update_service, "find_uv", lambda: "uv")
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: dist == "neonize")
    return site, db


@pytest.fixture
def commands(monkeypatch):
    """Record the child-process commands instead of running them."""
    recorded: list[list[str]] = []
    failing: set[str] = set()

    def fake_run(command, cwd, what):
        recorded.append(command)
        if what in failing:
            raise update_service.UpdateError(f"{what} failed")

    monkeypatch.setattr(update_service, "_run", fake_run)
    return recorded, failing


def test_install_kind_detects_each_install(tmp_path):
    """A .git dir is a checkout; a uv receipt or pipx metadata in the environment is a tool install."""
    env = tmp_path / "env"
    env.mkdir()
    assert update_service.install_kind(tmp_path, str(env)) is None
    (env / "pipx_metadata.json").write_text("{}")
    assert update_service.install_kind(tmp_path, str(env)) == update_service.PIPX
    (env / "uv-receipt.toml").write_text("")
    assert update_service.install_kind(tmp_path, str(env)) == update_service.UV_TOOL
    (tmp_path / ".git").mkdir()
    assert update_service.install_kind(tmp_path, str(env)) == update_service.CHECKOUT


def test_tool_install_version_and_eligibility(tool_install, monkeypatch):
    """Without pyproject.toml the installed distribution's version counts; uv must be on the PATH."""
    site, _ = tool_install
    assert update_service.installed_version(site) == (1, 0, 0)
    status = update_service.eligibility(site)
    assert status.eligible and status.kind == update_service.UV_TOOL
    monkeypatch.setattr(update_service, "find_uv", lambda: None)
    assert "uv is not on the PATH" in update_service.eligibility(site).reason


def test_fetch_release_reads_the_wheel_asset():
    """The release's wheel URL and digest are read next to the frontend asset."""
    release = update_service.fetch_release("v1.0.0", "o/r", client=_client())
    assert release.wheel_name == "janus_assistant-1.0.0-py3-none-any.whl"
    assert release.wheel_url.endswith("/v1.0.0/janus_assistant-1.0.0-py3-none-any.whl")
    assert release.wheel_digest == "sha256:" + hashlib.sha256(WHEEL_OLD).hexdigest()


def test_tool_install_commands_keep_the_installed_extras(tmp_path, monkeypatch):
    """Both uv and pipx reinstall from the wheel with the extras installed now."""
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: dist in {"neonize", "pystray"})
    wheel = tmp_path / "janus_assistant-1.1.0-py3-none-any.whl"
    uv = update_tool_install.install_command(update_service.UV_TOOL, "uv", wheel)
    assert uv[:4] == ["uv", "tool", "install", "--force"]
    assert uv[-1] == f"janus-assistant[desktop,whatsapp] @ {wheel.as_uri()}"
    pipx = update_tool_install.install_command(update_service.PIPX, "pipx", wheel)
    assert pipx == ["pipx", "install", "--force", f"{wheel}[desktop,whatsapp]"]

    monkeypatch.setattr(update_service, "_is_installed", lambda dist: False)
    assert update_tool_install.install_command(update_service.UV_TOOL, "uv", wheel)[-1] == str(wheel)


def test_apply_update_reinstalls_the_tool_and_keeps_the_old_wheel(tool_install, commands):
    """The new wheel is installed and migrated; the previous wheel is kept for a rollback."""
    site, db = tool_install
    recorded, _ = commands
    client = _client()
    release = update_service.check_for_update(site, client=client)

    installed = update_service.apply_update(release, site, client=client)

    assert installed.kind == update_service.UV_TOOL and installed.tag == "v1.1.0"
    kept = (
        update_service.state_dir(site)
        / update_tool_install.ROLLBACK_WHEEL_DIR
        / "janus_assistant-1.0.0-py3-none-any.whl"
    )
    assert installed.previous_wheel == str(kept) and kept.read_bytes() == WHEEL_OLD
    assert recorded[0][:4] == ["uv", "tool", "install", "--force"]
    assert (
        recorded[0][-1].startswith("janus-assistant[whatsapp] @ file://") and "janus_assistant-1.1.0" in recorded[0][-1]
    )
    assert [cmd[-1] for cmd in recorded[1:]] == [
        "import janus.main",
        "from janus import database; database.ensure_schema_up_to_date()",
    ]
    assert db.with_name(update_service.SNAPSHOT_NAME).is_file()


def test_failed_migration_reinstalls_the_previous_wheel_and_restores_the_database(tool_install, commands):
    """A failure after the install puts the previous wheel back and restores the snapshot."""
    site, db = tool_install
    recorded, failing = commands
    failing.add("database migration")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE marker (x)")
    conn.commit()
    conn.close()
    client = _client()
    release = update_service.check_for_update(site, client=client)

    original_run = update_service._run

    def migrate_then_fail(command, cwd, what):
        if what == "database migration":
            conn = sqlite3.connect(db)
            conn.execute("DROP TABLE marker")
            conn.commit()
            conn.close()
        original_run(command, cwd, what)

    update_service._run = migrate_then_fail
    try:
        with pytest.raises(update_service.UpdateError):
            update_service.apply_update(release, site, client=client)
    finally:
        update_service._run = original_run

    assert "janus_assistant-1.0.0-py3-none-any.whl" in recorded[-1][-1]
    tables = sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall()
    assert ("marker",) in tables


def test_failed_boot_of_a_tool_update_reinstalls_the_previous_wheel(tool_install, monkeypatch):
    """The boot recovery of a tool install reinstalls the kept wheel instead of moving git."""
    site, _ = tool_install
    monkeypatch.setattr(update_boot, "_confirmed", threading.Event())
    monkeypatch.setattr(update_service, "_restart_requested", threading.Event())
    monkeypatch.setattr(update_boot.event_logger, "log", lambda *a, **k: None)
    done = []
    monkeypatch.setattr(update_tool_install, "rollback", lambda kind, wheel, db: done.append((kind, wheel)))
    monkeypatch.setattr(update_service, "rollback", lambda *a: done.append("git rollback"))
    monkeypatch.setattr(update_service, "reexec", lambda: done.append("reexec"))

    installed = update_service.Installed("v1.1.0", "", None, kind=update_service.UV_TOOL, previous_wheel="/w.whl")
    update_boot.write_pending(installed, site)
    assert (update_service.state_dir(site) / "janus-update-pending.json").is_file()
    with pytest.raises(RuntimeError), update_boot.recover_on_failed_boot(site):
        raise RuntimeError("boot failed")

    assert done == [(update_service.UV_TOOL, "/w.whl"), "reexec"]
    assert update_boot.skipped_tag(site) == "v1.1.0"


def test_resources_fall_back_to_the_checkout(monkeypatch, tmp_path):
    """Without janus/_data (a checkout), the repository's alembic.ini and frontend/dist are used."""
    monkeypatch.setattr(resources, "PACKAGE_DATA_DIR", tmp_path / "_data")
    assert not resources.is_packaged_install()
    assert resources.alembic_ini_path() == resources.REPO_ROOT / "alembic.ini"
    assert resources.frontend_dist_dir() == resources.REPO_ROOT / "frontend" / "dist"
    (tmp_path / "_data" / "frontend").mkdir(parents=True)
    (tmp_path / "_data" / "alembic.ini").write_text("")
    assert resources.is_packaged_install()
    assert resources.alembic_ini_path() == tmp_path / "_data" / "alembic.ini"
    assert resources.frontend_dist_dir() == tmp_path / "_data" / "frontend"


def test_a_broken_download_leaves_no_file_behind(tmp_path):
    """A connection that drops mid-download, or a digest mismatch, leaves nothing at the target."""

    def dropping(request: httpx.Request) -> httpx.Response:
        def body():
            yield b"partial"
            raise httpx.ReadError("connection lost")

        return httpx.Response(200, content=body())

    client = httpx.Client(transport=httpx.MockTransport(dropping))
    digest = "sha256:" + "a" * 64
    with pytest.raises(update_service.UpdateError):
        update_service._download_asset("https://example.invalid/w.whl", digest, "w.whl", tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []

    good = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"other bytes")))
    with pytest.raises(update_service.UpdateError, match="SHA-256"):
        update_service._download_asset("https://example.invalid/w.whl", digest, "w.whl", tmp_path, client=good)
    assert list(tmp_path.iterdir()) == []


def test_a_corrupt_kept_wheel_is_downloaded_again(tool_install):
    """A kept rollback wheel that no longer matches its published digest is replaced."""
    site, _ = tool_install
    directory = update_service.state_dir(site) / update_tool_install.ROLLBACK_WHEEL_DIR
    directory.mkdir(parents=True)
    (directory / "janus_assistant-1.0.0-py3-none-any.whl").write_bytes(b"truncated")

    kept = update_tool_install._keep_rollback_wheel(site, client=_client())

    assert kept.read_bytes() == WHEEL_OLD


def test_pipx_reinstall_keeps_the_environment_suffix(tmp_path, monkeypatch):
    """A `pipx install --suffix` environment is reinstalled under the same suffix."""
    env = tmp_path / "janus-assistant@beta"
    env.mkdir()
    (env / "pipx_metadata.json").write_text('{"main_package": {"package": "janus-assistant", "suffix": "@beta"}}')
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: False)
    wheel = tmp_path / "janus_assistant-1.1.0-py3-none-any.whl"

    command = update_tool_install.install_command(update_service.PIPX, "pipx", wheel)

    assert command == ["pipx", "install", "--force", "--suffix=@beta", str(wheel)]


def test_docs_resolve_from_the_package_or_the_checkout(monkeypatch, tmp_path):
    """The in-app docs come from janus/_data/docs_root in a wheel, else from the repository."""
    monkeypatch.setattr(resources, "PACKAGE_DATA_DIR", tmp_path / "_data")
    assert resources.docs_root() == resources.REPO_ROOT
    (tmp_path / "_data" / "docs_root").mkdir(parents=True)
    assert resources.docs_root() == tmp_path / "_data" / "docs_root"


def test_uv_reinstall_keeps_the_packages_added_with_with(tmp_path, monkeypatch):
    """`uv tool install --with` packages from the receipt are passed back with their sources (JAN-409).

    The receipt below is the shape uv itself writes for each kind of source; reinstalling with
    the rebuilt arguments was checked to give back the same receipt.
    """
    env = tmp_path / "janus-assistant"
    env.mkdir()
    (env / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n"
        '    { name = "janus-assistant", extras = ["desktop"], url = "https://example.invalid/janus.whl" },\n'
        '    { name = "pygobject" },\n'
        '    { name = "six", marker = "python_full_version >= \'3.1\'", specifier = ">=1.16" },\n'
        '    { name = "rich", extras = ["jupyter"] },\n'
        '    { name = "mylib", url = "https://example.invalid/mylib.whl" },\n'
        '    { name = "localpkg", directory = "/src/localpkg" },\n'
        '    { name = "sample", git = "https://github.com/pypa/sampleproject?rev=main&subdirectory=pkg" },\n'
        '    { name = "edpkg", editable = "/src/edpkg" },\n'
        '    { name = "future", index = "https://example.invalid/simple" },\n'
        "]\n"
    )
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: dist == "pystray")
    wheel = tmp_path / "janus_assistant-1.1.0-py3-none-any.whl"

    command = update_tool_install.install_command(update_service.UV_TOOL, "uv", wheel)

    assert command[6:] == [
        "--with=pygobject",
        "--with=six>=1.16 ; python_full_version >= '3.1'",
        "--with=rich[jupyter]",
        "--with=mylib @ https://example.invalid/mylib.whl",
        "--with=localpkg @ file:///src/localpkg",
        "--with=sample @ git+https://github.com/pypa/sampleproject@main#subdirectory=pkg",
        "--with-editable=/src/edpkg",
        f"janus-assistant[desktop] @ {wheel.as_uri()}",
    ]  # `future` has a key this cannot rebuild, so it is left out rather than passed as a bare name


@pytest.mark.parametrize("receipt", [None, "[tool]\n", "not toml [", '[tool]\nrequirements = "x"\n'])
def test_uv_reinstall_without_readable_extra_packages_adds_none(tmp_path, monkeypatch, receipt):
    """A missing, empty or broken receipt adds no `--with`."""
    if receipt is not None:
        (tmp_path / "uv-receipt.toml").write_text(receipt)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: False)
    wheel = tmp_path / "janus_assistant-1.1.0-py3-none-any.whl"

    command = update_tool_install.install_command(update_service.UV_TOOL, "uv", wheel)

    assert not any(arg.startswith("--with") for arg in command)


def test_pipx_reinstall_keeps_the_injected_packages(tmp_path, monkeypatch):
    """`pipx inject` packages, which `pipx install --force` drops, are passed back as `--preinstall` (JAN-409)."""
    env = tmp_path / "janus-assistant"
    env.mkdir()
    (env / "pipx_metadata.json").write_text(
        '{"main_package": {"package": "janus-assistant", "suffix": ""},'
        ' "injected_packages": {"pygobject": {"package_or_url": "pygobject==3.58.1"}, "rich": {}}}'
    )
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(update_service, "_is_installed", lambda dist: False)
    wheel = tmp_path / "janus_assistant-1.1.0-py3-none-any.whl"

    command = update_tool_install.install_command(update_service.PIPX, "pipx", wheel)

    assert command == ["pipx", "install", "--force", "--preinstall=pygobject==3.58.1", "--preinstall=rich", str(wheel)]

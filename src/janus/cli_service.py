"""The `janus service` commands: run Janus as a systemd user service (JAN-399)."""

import getpass
import shutil
import subprocess

import typer

from janus.services import systemd_unit

service_app = typer.Typer(help="Run Janus in the background as a systemd user service.")


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    return typer.Exit(1)


@service_app.command("install")
def install(
    start: bool = typer.Option(True, "--start/--no-start", help="Start the service right away."),
):
    """Install a user service for `janus start`, enable it at login, and start it."""
    try:
        path = systemd_unit.install(start=start)
    except systemd_unit.ServiceError as exc:
        raise _fail(exc) from exc
    typer.secho(f"Installed {path}.", fg=typer.colors.GREEN)
    typer.echo("Janus now starts when you log in. To start it at boot without a login, run:")
    typer.echo(f"  loginctl enable-linger {getpass.getuser()}")


@service_app.command("uninstall")
def uninstall():
    """Stop and disable the service and remove its unit file."""
    try:
        removed = systemd_unit.uninstall()
    except systemd_unit.ServiceError as exc:
        raise _fail(exc) from exc
    typer.echo("Removed the Janus service." if removed else "No Janus service is installed.")


@service_app.command("status")
def status():
    """Show whether the service is running (systemctl --user status)."""
    try:
        result = systemd_unit.systemctl("status", "--no-pager", systemd_unit.UNIT_NAME, check=False)
    except systemd_unit.ServiceError as exc:
        raise _fail(exc) from exc
    typer.echo((result.stdout or result.stderr).rstrip())
    raise typer.Exit(result.returncode)


@service_app.command("logs")
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep printing new log lines."),
    lines: int = typer.Option(100, "--lines", "-n", help="How many recent lines to show."),
):
    """Show the service's log (journalctl --user)."""
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        raise _fail(systemd_unit.ServiceError("journalctl is not installed"))
    command = [journalctl, "--user", "-u", systemd_unit.UNIT_NAME, "-n", str(lines), "--no-pager"]
    raise typer.Exit(subprocess.call([*command, *(["-f"] if follow else [])]))

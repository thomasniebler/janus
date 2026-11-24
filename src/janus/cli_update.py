"""The `janus update` command: install the latest GitHub release into this checkout."""

import typer

from janus.services import update_service


def update(
    check: bool = typer.Option(False, "--check", help="Only report whether a newer release exists."),
):
    """Install the latest GitHub release into this git checkout (restart Janus afterwards)."""
    status = update_service.eligibility()
    current = update_service.format_version(update_service.installed_version())
    try:
        release = update_service.check_for_update()
    except update_service.UpdateError as exc:
        typer.secho(f"Update check failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if release is None:
        typer.echo(f"Janus {current} is the latest release.")
        return
    typer.echo(f"Janus {release.tag} is available (installed: {current}).")
    if check:
        typer.echo(f"Auto-update: {status.reason}.")
        return
    try:
        update_service.apply_update(release)
    except update_service.UpdateError as exc:
        typer.secho(f"Update failed and was rolled back: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Installed {release.tag}. Restart any running Janus to use it.", fg=typer.colors.GREEN)

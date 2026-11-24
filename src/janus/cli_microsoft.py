"""``janus microsoft ...``: sign in to Microsoft 365 for Outlook mail, calendar, and Teams chats."""

import typer

from janus.services import microsoft_auth_service

microsoft_app = typer.Typer(help="Sign in to Microsoft 365 for Outlook mail, calendar, and Teams chats.")


@microsoft_app.command("login")
def microsoft_login():
    """Sign in to Microsoft 365 in your browser (once). Opens a browser window on this machine."""
    try:
        username = microsoft_auth_service.login()
    except microsoft_auth_service.MicrosoftAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.echo(f"Signed in as {username}. Run `janus microsoft status` to see the granted permissions.")


@microsoft_app.command("status")
def microsoft_status():
    """Show whether Microsoft 365 is connected, as whom, and which Graph permissions the token carries."""
    status = microsoft_auth_service.connection_status()
    if not status["configured"]:
        typer.echo("Not configured: set JANUS_MICROSOFT_CLIENT_ID and JANUS_MICROSOFT_TENANT_ID.")
        raise typer.Exit(1)
    if not status["connected"]:
        typer.echo("Configured, not signed in: run `janus microsoft login`.")
        raise typer.Exit(1)
    if status["error"]:
        typer.secho(status["error"], fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"Signed in as {status['account']}")
    typer.echo("Permissions: " + (", ".join(status["scopes"]) or "none reported"))


@microsoft_app.command("logout")
def microsoft_logout():
    """Forget the signed-in Microsoft account so Janus stops using it."""
    if microsoft_auth_service.logout():
        typer.echo("Signed out. The encrypted token cache was deleted.")
    else:
        typer.echo("Not signed in.")

# Desktop widget

Janus provides a small authenticated API and CLI output for desktop bars such
as Waybar and Sway.

## API

`GET /api/desktop/summary` uses the same bearer token as the Janus CLI. The
response contains aggregate counts only:

```json
{"open_tasks": 2, "unread_emails": 4}
```

`open_tasks` counts unfinished Janus-owned planner tasks. Imported tasks from
synced planner accounts (currently Google Calendar/Tasks and Jira) are not
included. By
default, `unread_emails` counts unread email records belonging to the
authenticated user's email accounts. Add
`?include_unread_emails=false` to omit that count; the JSON value is then
`null`.

The endpoint requires `Authorization: Bearer <token>` and never reads the
database directly from a desktop client.

## Waybar and Sway

Log in once so the CLI can cache a reusable bearer token for desktop summary
requests:

```bash
janus login <username>
```

After a successful login, Janus stores the token response in
`~/.config/janus/token.json` (or `$XDG_CONFIG_HOME/janus/token.json`) with
user-only (`0600`) permissions. If you are upgrading from an older Janus
release, the CLI migrates the legacy `~/.janus_token` cache on first use so
existing desktop widgets keep working. Keep the token file private;
`janus desktop-summary` reuses it for authenticated requests, and removing the
active cache file signs the CLI out.

Then add the CLI command as a Waybar custom module:

```json
"custom/janus": {
  "exec": "janus desktop-summary",
  "return-type": "json",
  "interval": 60,
  "on-click": "xdg-open http://localhost:8080"
}
```

The command prints valid Waybar JSON even when Janus is unavailable, using an
`unavailable` class so the bar does not display a traceback. Use
`janus desktop-summary --no-unread-emails` when only planner tasks are wanted.

If the backend returns `401 Unauthorized`, Janus clears the cached token and
the widget will stay in its `unavailable` state until you run `janus login`
again.

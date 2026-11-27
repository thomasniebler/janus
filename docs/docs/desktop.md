# Desktop

When Janus runs on your own machine, it can sit in the system tray, feed a
status bar, pop up notifications, and drive a task sidebar. None of this is
needed on a server.

## Desktop mode and server mode

`janus start` runs in desktop mode unless you ask for server mode:

```bash
janus start                  # desktop mode (default)
janus start --tray           # desktop mode with a tray icon
janus start --server-mode    # no tray icon, no desktop notifications
```

Server mode is also set with `JANUS_SERVER_MODE=true` (see
[Configuration](configuration.md)). Use it on any machine without a desktop
session. `--desktop-mode` overrides the setting for one start.

## Tray icon

`janus start --tray` adds a tray icon with two entries:

- **Open Dashboard** opens Janus in your browser.
- **Quit** stops Janus.

The icon needs:

- the `desktop` extra (`uv tool install "janus-assistant[desktop]"`, or
  `uv sync --extra desktop` in a checkout);
- a graphical session (`DISPLAY` or `WAYLAND_DISPLAY` set).

Without either, Janus logs why and runs on without the icon. Server mode
ignores `--tray`.

On Wayland (Hyprland included), Janus uses pystray's AppIndicator backend,
which needs more than the `desktop` extra installs:

- PyGObject in Janus's own Python environment, for example
  `uv tool install "janus-assistant[desktop]" --with PyGObject` (it builds
  against your system's GObject introspection and Cairo development files);
- the GTK 3 and AppIndicator (or Ayatana AppIndicator) introspection data from
  your distribution;
- a tray in your bar, such as Waybar's `tray` module.

If any of these is missing, Janus logs a warning and runs without the icon.

## Desktop notifications

In desktop mode on Linux, Janus calls `notify-send` for:

| Notification | When |
| --- | --- |
| **Janus: Important Mail from …** | A new mail is sorted as Important. |
| **Janus: Daily Digest** | The afternoon digest finishes. |
| **Janus started a conversation** | A trigger run opened a new chat with you (see [Chat](chat.md#conversations-janus-starts)). |
| A critical alert | A self-evolution self-check failed. |

`notify-send` comes from libnotify (`libnotify-bin` on Debian and Ubuntu). On
Hyprland, a notification daemon such as mako, dunst, or swaync shows them.
Server mode turns all of them off.

These popups come from the Janus process itself, so they only reach a desktop
that runs the server. For a desktop that talks to a remote Janus, use the
[action inbox popup](#action-inbox-popups) below.

## Status bar widget

`janus desktop-summary` prints one line of JSON for status bars such as Waybar:

```json
{"text": "2 tasks · 4 unread", "alt": "Janus", "class": "normal", "tooltip": "Janus: 2 tasks, 4 unread"}
```

- **tasks** counts your unfinished local Janus tasks (not tasks imported from
  Google or Jira).
- **unread** counts unread mail in your mailboxes. Leave it out with
  `--no-unread-emails`.

If Janus cannot be reached, the command still prints valid JSON with the text
`Janus unavailable` and the class `unavailable`, so the bar shows no traceback.

### Set up

Log in once so the CLI caches a token:

```bash
janus login <username>
```

The token is saved in `~/.config/janus/token.json` (or
`$XDG_CONFIG_HOME/janus/token.json`), readable only by you. Point the CLI at a
remote Janus with `--url` or `JANUS_URL`. When Janus answers `401`, the CLI
deletes the cached token, and the widget shows `unavailable` until you log in
again.

Add a Waybar module:

```json
"custom/janus": {
  "exec": "janus desktop-summary",
  "return-type": "json",
  "interval": 60,
  "on-click": "xdg-open http://localhost:8080"
}
```

Style the `unavailable` class in your Waybar CSS (`#custom-janus.unavailable`)
if you want a visible warning.

### The API behind it

`GET /api/desktop/summary` returns the raw counts for any widget you write
yourself:

```json
{"open_tasks": 2, "unread_emails": 4}
```

It takes `Authorization: Bearer <token>` (the token from `janus login`).
Add `?include_unread_emails=false` and `unread_emails` is `null`.

## Action inbox popups

This script polls the [action inbox](inbox.md#the-action-inbox) with a device
token and shows each new item as a notification with buttons: **Open in Janus**
and the item's own action (`mark_read` or `dismiss`). It works against a local
or remote Janus. It needs `curl`, `jq`, and `notify-send` from libnotify 0.8 or
newer (older versions have no action buttons).

Create a device token first (see [Device tokens](inbox.md#device-tokens)), then:

```bash
mkdir -p ~/.config/janus && install -m 600 /dev/null ~/.config/janus/inbox-token
$EDITOR ~/.config/janus/inbox-token   # paste the token
JANUS_URL=https://janus.example.com ./janus-inbox-popup.sh
```

```bash
#!/usr/bin/env bash
# Show each new Janus inbox item as a desktop notification with action buttons.
# Needs: curl, jq, and notify-send from libnotify 0.8 or newer (for -A actions).
set -euo pipefail

JANUS_URL="${JANUS_URL:-http://localhost:8080}"
TOKEN_FILE="${JANUS_TOKEN_FILE:-$HOME/.config/janus/inbox-token}"
INTERVAL="${JANUS_POLL_SECONDS:-60}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/janus-inbox"
mkdir -p "$STATE"
touch "$STATE/seen"
token="$(<"$TOKEN_FILE")"
etag=""

# Title and summary come from mail, calendars, and models: escape them so a
# notification daemon shows them literally and never as markup or a link.
escape_markup() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

handle_item() {
  local item="$1" kind id title summary open_url choice
  kind="$(jq -r .kind <<<"$item")"
  id="$(jq -r .id <<<"$item")"
  title="$(jq -r .title <<<"$item" | escape_markup)"
  summary="$(jq -r .summary <<<"$item" | escape_markup)"
  open_url="$(jq -r .open_url <<<"$item")"
  local args=(-A "open=Open in Janus")
  while read -r action; do
    [ -n "$action" ] && args+=(-A "$action=${action//_/ }")
  done < <(jq -r '.actions[]' <<<"$item")
  choice="$(notify-send "${args[@]}" -- "$title" "$summary" || true)"
  case "$choice" in
    open) xdg-open "$JANUS_URL$open_url" >/dev/null 2>&1 || true ;;
    mark_read | dismiss)
      curl -fsS -X POST -H "Authorization: Bearer $token" \
        "$JANUS_URL/api/inbox/$kind/$id/$choice" >/dev/null || true
      ;;
  esac
}

while true; do
  headers="$STATE/headers"
  code="$(curl -sS -o "$STATE/body" -D "$headers" -w '%{http_code}' \
    -H "Authorization: Bearer $token" ${etag:+-H "If-None-Match: $etag"} \
    "$JANUS_URL/api/inbox" || echo 000)"
  if [ "$code" = 200 ]; then
    etag="$(awk -F': ' 'tolower($1)=="etag"{print $2}' "$headers" | tr -d '\r')"
    while read -r item; do
      key="$(jq -r '"\(.kind):\(.id)"' <<<"$item")"
      grep -qxF "$key" "$STATE/seen" && continue
      echo "$key" >>"$STATE/seen"
      handle_item "$item" &   # a notification waits for a click; keep polling
    done < <(jq -c '.items[]' "$STATE/body")
  elif [ "$code" = 401 ]; then
    echo "Janus refused the token (revoked or wrong); stopping." >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
```

`JANUS_POLL_SECONDS` sets the interval (default 60). The script stops when
Janus refuses the token, for example after you revoked it. Start it from your
session, for example with `exec-once = /path/to/janus-inbox-popup.sh` in
`hyprland.conf` or as a systemd user service.

For an `eww` widget, call the same endpoint from a `defpoll` with `curl` and
`jq`, and render `title`, `summary`, and one button per entry in `actions`.
Janus does not ship an `eww` configuration.

## Todo Chat companion

Todo Chat is an optional desktop sidebar (Tauri and React) for your local Janus
tasks and chat. Janus stays the source of truth: the sidebar has no task
database of its own, and it works with a local or a remote Janus.

It shows:

- **Tasks**: your local Janus tasks as a tree, with an urgency filter, **Add
  task**, and **Add subtask**;
- **Details**: the selected task's title, notes, and urgency, with **Save
  changes** and **Mark done**;
- **Chat**: a chat with Janus in a new or an existing conversation. Janus
  changes tasks there through its planner tools.

Tasks imported from Google or Jira are not shown; they are read-only in Janus.

### Install on Hyprland

From a Janus git checkout, with Node.js 18 or newer, Rust, and the Tauri
build libraries (WebKitGTK, GTK 3, AppIndicator, librsvg) installed:

```bash
task todo-chat:install
```

This runs `scripts/install-todo-chat.sh`, which:

1. builds the release binary and installs it as `~/.local/bin/todo-chat`;
2. installs and starts the `todo-chat.service` systemd user unit;
3. creates `~/.config/janus/config.toml` if it is missing;
4. writes `~/.config/hypr/conf.d/janus-companion.conf` (a `Super+T` toggle and
   window rules for a 440 px, full-height panel pinned to the right edge),
   sources it from `hyprland.conf`, and runs `hyprctl reload`.

The installer removes older Todo Chat lines from your Hyprland config, so one
press of `Super+T` always toggles once.

### Connect

Set the Janus address in `~/.config/janus/config.toml`:

```toml
base_url = "https://janus.example.com"
```

Press `Super+T`, enter your Janus username and password, and press
**Connect**. The session is kept in the system keyring; your password is used
only to log in. If the saved session expired or was revoked, the sidebar
returns to the login screen.

The process stays resident and starts hidden. `todo-chat --toggle` shows or
hides it, and `Escape` hides it. If the window does not appear, check
`systemctl --user status todo-chat`.

Build and development commands are in `modules/todo-chat/README.md` in the
repository.

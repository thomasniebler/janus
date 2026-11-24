# Action inbox

The action inbox is one list of what is waiting for you: conversations Janus
started and you have not opened, plus planner suggestions, topic suggestions,
mail rule suggestions, and automations Janus proposed. It exists so a desktop
popup or a phone can show what the [Secretary](runtime-triggers.md) found
without you opening the dashboard.

## What it lists, and what a device can do

| Item | Device actions | Everything else |
| --- | --- | --- |
| Conversation Janus started, unread | `mark_read` | Open it in Janus (`open_url`) |
| Planner suggestion | `dismiss` | Approve in the Planner |
| Topic suggestion | `dismiss` | Approve in Memory → Topics |
| Mail rule suggestion | `dismiss` | Approve in Memory → Mail Prioritization Rules |
| Automation Janus proposed | none (`risk` is `needs_web`) | Review in Settings → Triggers |

A device can only open an item or dismiss it, and only while it is still open (an item you already approved cannot be dismissed from a device). **Approving stays in the web
app**, because an approval can let an unattended run use a tool or change how
Janus behaves, and a token on a laptop should not be able to do that.

## Create a device token

Open **Settings → Daily Briefing → Desktop and device access**, give the device
a name (for example "Laptop popup"), and press **Create token**. The token
starts with `jdt_` and is **shown once**; Janus keeps only a hash of it. Create
one token per device, up to ten, so a lost device is one revocation: press
**Revoke** next to its name and it stops working at once.

Changing your password or revoking your sessions also revokes every device token, and deleting a user deletes theirs, so recovering an account closes its devices too. A device token opens only the inbox. Every other part of Janus answers 401 to
it, and it cannot create or revoke tokens. Treat it like a password anyway:
store it in a file only you can read (`chmod 600`).

## The API

```text
GET  /api/inbox                          -> {"items": [...]}   newest first, at most 50
POST /api/inbox/{kind}/{id}/{action}     -> 204
Authorization: Bearer jdt_...
```

Each item has `kind`, `id`, `title`, `summary`, `created_at`, `risk` (`low` or
`needs_web`), `actions`, and `open_url` (a path inside Janus, to append to your
Janus address). `title` and `summary` come from mail, calendar, and models:
**show them as plain text**, never as markup or links.

`GET /api/inbox` sends an `ETag`. Send it back as `If-None-Match` and an
unchanged inbox answers `304` with no body, so polling once a minute costs
almost nothing. An unknown item, someone else's item, or an action that does
not exist for that kind answers `404`.

## A desktop popup with buttons

This script polls the inbox, shows each new item as a desktop notification with
**Open in Janus** and the item's action as buttons, and sends the action you
click. It needs `curl`, `jq`, and `notify-send` from libnotify 0.8 or newer
(older versions have no action buttons).

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

# Notification daemons may render markup (bold, links) in the body. Title and summary come from mail, calendar,
# and models, so escape them to show literally and never as a clickable link.
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
      handle_item "$item" &   # a notification waits for a click; do not block the polling
    done < <(jq -c '.items[]' "$STATE/body")
  elif [ "$code" = 401 ]; then
    echo "Janus refused the token (revoked or wrong); stopping." >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
```

Set `JANUS_URL` to the same address as `base_url` in your
[desktop companion configuration](configuration.md#desktop-companion-configuration),
and `JANUS_POLL_SECONDS` to poll more or less often (default 60). The script
stops with an error if Janus refuses the token, for example after you revoked
it. Start it from your session (a systemd user service, or your window
manager's autostart).

The script was run against a stand-in for the API (first poll, one popup, the
dismiss request, `304` polls, and the stop on `401`); it has not been run
against a real notification daemon. For an `eww` widget, call the same
endpoint from a `defpoll` with `curl` and `jq` and render `title`, `summary`,
and one button per entry in `actions`; Janus does not ship an `eww`
configuration.

## Phone

WhatsApp alerts for time-critical items already work
([Notify me on WhatsApp](tools.md#notify-me-on-whatsapp)). The iOS app does not
show the inbox or receive push notifications yet; push needs an Apple
developer account and an APNs key, and is planned separately. See the
[feature spec](feature-specs/action-inbox.md).

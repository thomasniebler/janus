# Janus iOS App

This directory contains a native SwiftUI iOS client for Janus.

## Prerequisites

- Xcode 16+
- [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`)

## Generate and run

```bash
cd ios/JanusMobile
xcodegen generate
open JanusMobile.xcodeproj
```

Then choose an iOS simulator and run the `JanusMobile` target.

## Configuration

Set the Janus backend URL in the app's **Settings** screen (default: `http://localhost:8080`).
Use `https://` for remote servers; `http://` is accepted only for localhost development URLs.

This is intentional (see `AppSettings.isValidBaseURL`): the app refuses to send
your username/password and session cookie over plain HTTP to anything other
than `localhost`/`127.0.0.1`. A real iPhone has no `localhost` Janus server of
its own, so **you must put an HTTPS endpoint in front of your Janus backend**
before the app is useful off the simulator — see below.

## Chat

The **Chat** tab talks to the same plan-driven agent runtime as the web
dashboard's "Chat with Janus" panel, over `/ws/chat`, and speaks as the
user's Janus character (Settings → Character in the web app). REST calls keep using the cookie session set on
login; the chat WebSocket authenticates separately via the JWT from the login
response (kept in the Keychain, see `TokenStore.swift`), since cookie
forwarding on a WebSocket handshake isn't as reliable as on plain HTTP
requests. Only one conversation is shown at a time — tap the compose icon to
start a new one; conversation history (visible on the web dashboard) isn't in
this first mobile version.

**Backend requirement — allow-list the app's origin:** the backend rejects any
WebSocket handshake whose `Origin` header isn't in its `allowed_origins`
setting (`janus/request_origin.py`); a browser sends this automatically, but a
native client doesn't, so the app sends one derived from the configured API
URL itself (e.g. `http://localhost:8080`, or `https://janus.example.com`). If
Chat shows "Chat disconnected: Invalid Origin header", add that exact origin
to `allowed_origins` in the backend's `settings.toml` and restart it. A
deployment that already serves its web dashboard from that same origin (the
usual case — see the `allowed_origins` comment in `settings.toml`) needs no
extra configuration.

## Running on a physical iPhone

You need a Mac with Xcode; this repo/CLI environment cannot build or sign iOS
apps. Steps on your Mac:

1. Install [Xcode](https://apps.apple.com/app/xcode/id497799835) (App Store)
   and `brew install xcodegen`.
2. `cd ios/JanusMobile && xcodegen generate && open JanusMobile.xcodeproj`.
3. In Xcode → Settings → Accounts, sign in with your Apple ID (a free account
   works, no paid Developer Program needed for personal device testing).
4. Select the `JanusMobile` target → **Signing & Capabilities** → check
   "Automatically manage signing" → choose your personal team.
5. Connect the iPhone via USB (or enable Wi-Fi debugging), trust the Mac when
   prompted on the phone, then pick the iPhone as the run destination and
   press ⌘R.
6. On first launch, iOS will refuse to run the app: on the iPhone go to
   **Settings → General → VPN & Device Management**, and trust your Apple ID's
   developer certificate. Re-run the app from Xcode afterward.
7. With a free Apple ID the build expires after **7 days** — reopen Xcode and
   re-run (⌘R) to renew it. A paid Developer Program membership (or
   TestFlight) removes that limit.

### Reaching your Janus server from the phone

Your iPhone and the Janus server don't share `localhost`, and (per above) the
app won't send credentials over plain HTTP to a non-localhost host. Pick one:

- **Reverse proxy with a real certificate** — put Janus behind Caddy/nginx/
  Traefik on a domain you control; Caddy gets you automatic HTTPS with one
  line (`your-domain.example.com { reverse_proxy localhost:8080 }`).
- **Tailscale** — join the Mac/server and the iPhone to the same tailnet, then
  use `tailscale serve` to expose Janus over HTTPS on the tailnet's `*.ts.net`
  hostname. No public exposure required.
- **Cloudflare Tunnel** (`cloudflared`) — exposes a local port over HTTPS
  through a Cloudflare-issued hostname without opening router ports.

Whichever you choose, put the resulting `https://…` URL into the app's
Settings screen.

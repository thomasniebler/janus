# Todo Chat companion sidebar

Todo Chat is an optional Tauri 2 + React desktop companion for a Janus server,
local or remote. It is deliberately a Janus module: Janus remains the source
of truth for tasks, conversations, authentication, and chat. The companion
does not create a second task database. User-facing docs live in
`docs/docs/desktop.md` (Todo Chat companion).

## Prerequisites

- Janus configured and running, locally or remotely (normally
  `http://localhost:8080` for a local server).
- Node.js 18 or newer and npm.
- Rust and Cargo.
- Linux desktop development libraries for Tauri/WebKitGTK. On Ubuntu/Debian:

  ```sh
  sudo apt-get install pkg-config libdbus-1-dev libwebkit2gtk-4.1-dev \
    libgtk-3-dev libappindicator3-dev librsvg2-dev patchelf
  ```

The packaged window stores its authenticated session material in the desktop
keyring. Passwords are used only during login and are never placed in browser
storage.

## Development

From the Janus repository root:

```sh
cd modules/todo-chat
npm --prefix frontend install
frontend/node_modules/.bin/tauri dev --config src-tauri/tauri.conf.json
```

The first launch opens a hidden resident window. Run the compiled process with
`--toggle` to show or hide it:

```sh
modules/todo-chat/src-tauri/target/debug/todo-chat --toggle
```

For a frontend-only preview, use Vite:

```sh
npm --prefix modules/todo-chat/frontend run dev
```

The browser preview can render the login surface, but Janus API calls require
the Tauri desktop shell. For browser development, allow `http://localhost:1420`
in Janus's `allowed_origins` setting.

## Connect to Janus

1. Start Janus and complete its normal first-run setup.
2. Launch Todo Chat.
3. Enter the Janus base URL, your Janus username, and password.
4. Click **Connect**.
5. In the **Chat** pane, pick **New conversation** or an existing conversation
   from the **Conversation** list. Chat always speaks as your Janus character;
   there is no agent to choose.

In chat, Janus reads your local tasks through the `planner.local` toolbox and
changes them through `planner.local.write`, which asks for your approval in
chat (see *Read local planner* and *Change local planner* in
`docs/docs/tools.md`). The sidebar's task list and task controls call the
authenticated user's local-task API directly and do not depend on chat.

## Build and install the desktop binary

On Hyprland, the supported setup is a fixed right-edge companion sidebar. From
the repository root, this one command builds the release binary, installs and
enables the resident user service, creates `~/.config/janus/config.toml` when
needed, and installs a `Super+T` toggle plus the sidebar window rules:

```sh
task todo-chat:install
```

The installer manages `~/.config/hypr/conf.d/janus-companion.conf` and sources
it from `hyprland.conf`; it removes older Todo Chat bindings/rules so one press
of `Super+T` always performs exactly one toggle. The panel is fixed at 440 px
wide, full monitor height, and aligned to the right edge. It is intentionally
not configurable yet.

Todo Chat reads its endpoint from the shared Janus config, not from a compiled
default or an environment variable:

```toml
# ~/.config/janus/config.toml
base_url = "https://janus.example.com"
```

`base_url` is global. Chat always speaks as your Janus character, so there is
no agent to choose; an old `[todo-chat] agent_id` entry is ignored. A valid
global URL takes precedence over a stale URL saved in the desktop keyring.

For a manual build without the installer:

```sh
npm --prefix modules/todo-chat/frontend install
cd modules/todo-chat
frontend/node_modules/.bin/tauri build --bundles deb
```

Install the resulting package for your distribution, or copy the built binary
to `~/.local/bin/todo-chat`. The included user service is at
`modules/todo-chat/packaging/todo-chat.service`.

## Seamless Hyprland integration

Install the service and enable it for the graphical user session:

```sh
mkdir -p ~/.config/systemd/user
cp modules/todo-chat/packaging/todo-chat.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now todo-chat.service
```

The service expects the installed binary at `~/.local/bin/todo-chat`. Adjust
`ExecStart` in the unit if you install it elsewhere. Add a global toggle and
window rules to `~/.config/hypr/hyprland.conf`:

```ini
exec-once = systemctl --user start todo-chat.service
bind = SUPER, T, exec, /home/YOUR_USER/.local/bin/todo-chat --toggle
windowrule = match:class ^(todo-chat)$, float on
windowrule = match:class ^(todo-chat)$, size 440 monitor_h
windowrule = match:class ^(todo-chat)$, move monitor_w-window_w 0
windowrule = match:class ^(todo-chat)$, pin on
windowrule = match:class ^(todo-chat)$, animation slide right
```

The exact Hyprland syntax varies by release; keep the equivalent syntax used
by the rest of your local config. On Linux, Tauri exposes the binary name as
the window class: `todo-chat`. The app stays resident and hidden, while
`Super+T` slides the right-edge sidebar in or out. `Escape` hides the window
without stopping the process.

## Troubleshooting

- **Only `index.html` appears:** do not open the file directly. Run Vite or
  Tauri from the module commands above.
- **`cargo tauri` is unavailable:** use the module-local
  `frontend/node_modules/.bin/tauri` command, or install the Tauri CLI as a
  development dependency with `npm --prefix modules/todo-chat/frontend install`.
- **GTK/WebKit build errors:** install the Ubuntu/Debian packages in
  [Prerequisites](#prerequisites), then rerun the build.
- **Login succeeds but tasks are empty:** confirm the account has Janus-owned
  local tasks and that Janus is serving the local task tree endpoint. Tasks
  imported from Google or Jira are not shown.
- **The window does not appear:** check `systemctl --user status todo-chat`
  and send `todo-chat --toggle` once more; the resident process intentionally
  starts hidden.

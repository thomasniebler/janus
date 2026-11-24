#!/usr/bin/env bash
# Build and install the Janus Todo Chat companion for a Hyprland session.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_root="${XDG_CONFIG_HOME:-${HOME}/.config}"
hypr_root="${config_root}/hypr"
janus_config_root="${config_root}/janus"
companion_rules="${hypr_root}/conf.d/janus-companion.conf"
hypr_config="${hypr_root}/hyprland.conf"
keybinds_config="${hypr_root}/conf.d/keybinds.conf"
rules_config="${hypr_root}/conf.d/rules.conf"
autostart_config="${hypr_root}/conf.d/autostart.conf"
companion_source_line="source = ${companion_rules}"
binary_source="${repo_root}/modules/todo-chat/src-tauri/target/release/todo-chat"
binary_target="${HOME}/.local/bin/todo-chat"
service_target="${config_root}/systemd/user/todo-chat.service"

if [[ ! -f "${hypr_config}" ]]; then
  echo "Hyprland configuration not found at ${hypr_config}" >&2
  exit 1
fi

echo "Building Todo Chat release binary…"
npm --prefix "${repo_root}/modules/todo-chat/frontend" ci
(
  cd "${repo_root}/modules/todo-chat"
  # Use Tauri's release builder so the frontend is embedded via the custom
  # protocol; a bare Cargo build would try to load Vite from localhost.
  frontend/node_modules/.bin/tauri build --bundles deb
)

systemctl --user stop todo-chat.service 2>/dev/null || true
install -Dm755 "${binary_source}" "${binary_target}"
install -Dm644 "${repo_root}/modules/todo-chat/packaging/todo-chat.service" "${service_target}"

mkdir -p "${janus_config_root}"
if [[ ! -f "${janus_config_root}/config.toml" ]]; then
  cat >"${janus_config_root}/config.toml" <<'EOF'
# Shared Janus endpoint used by local companion applications.
base_url = "http://localhost:8080"

[todo-chat]
# agent_id = 42
EOF
  echo "Created ${janus_config_root}/config.toml; set base_url before connecting."
fi

# Remove only prior Todo Chat entries; leave unrelated user configuration intact.
for config in "${hypr_config}" "${keybinds_config}" "${rules_config}" "${autostart_config}"; do
  if [[ -f "${config}" ]]; then
    sed -i '/todo-chat/d' "${config}"
  fi
done

mkdir -p "$(dirname "${companion_rules}")"
{
  cat <<'EOF'
# Managed by task todo-chat:install.
# Janus Companion: fixed right-edge, full-height Todo Chat sidebar.
EOF
  printf 'bind = SUPER, T, exec, %s --toggle\n' "${binary_target}"
  cat <<'EOF'
windowrule = match:class (?i)^(todo-chat)$, float on
windowrule = match:class (?i)^(todo-chat)$, size 440 monitor_h
windowrule = match:class (?i)^(todo-chat)$, move monitor_w-window_w 0
windowrule = match:class (?i)^(todo-chat)$, pin on
windowrule = match:class (?i)^(todo-chat)$, animation slide right
EOF
} >"${companion_rules}"

if ! grep -Fqx "${companion_source_line}" "${hypr_config}"; then
  printf '\n%s\n' "${companion_source_line}" >>"${hypr_config}"
fi

if command -v hyprctl >/dev/null && hyprctl instances -j >/dev/null 2>&1; then
  hyprctl reload
fi

systemctl --user daemon-reload
systemctl --user enable --now todo-chat.service

echo "Installed Todo Chat. Toggle the Janus Companion sidebar with Super+T."

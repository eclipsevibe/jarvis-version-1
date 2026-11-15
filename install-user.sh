#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin"
install -m 0644 "$ROOT/deploy/jarvis-llm.service" "$HOME/.config/systemd/user/jarvis-llm.service"
install -m 0644 "$ROOT/deploy/jarvis.service" "$HOME/.config/systemd/user/jarvis.service"
install -m 0755 "$ROOT/scripts/jarvis" "$HOME/.local/bin/jarvis"
mkdir -p "$HOME/.local/share/cinnamon/desklets"
cp -a "$ROOT/jarvis-hud@local" "$HOME/.local/share/cinnamon/desklets/"
"$ROOT/set-wallpaper.sh"

CURRENT_DESKLETS="$(gsettings get org.cinnamon enabled-desklets)"
ENABLED_DESKLETS="$(python3 - "$CURRENT_DESKLETS" <<'PY'
import ast, sys
try: desklets=ast.literal_eval(sys.argv[1])
except Exception: desklets=[]
entry='jarvis-hud@local:1:0:0'
if not any(item.startswith('jarvis-hud@local:') for item in desklets):
    desklets.append(entry)
print('['+', '.join(repr(item) for item in desklets)+']')
PY
)"
gsettings set org.cinnamon enabled-desklets "$ENABLED_DESKLETS"

# Cinnamon keybindings live in org.cinnamon.desktop.keybindings. Use two
# independent custom entries so either shortcut can toggle the same IPC HUD.
SCHEMA=org.cinnamon.desktop.keybindings
BASE=/org/cinnamon/desktop/keybindings/custom-keybindings
CURRENT="$(gsettings get "$SCHEMA" custom-list)"
CUSTOM_LIST="$(python3 - "$CURRENT" "$BASE" <<'PY'
import ast, sys
try: paths=ast.literal_eval(sys.argv[1])
except Exception: paths=[]
base=sys.argv[2]
for n in ('custom0','custom1'):
    path=f'{base}/{n}/'
    if path not in paths: paths.append(path)
print('['+', '.join(repr(p) for p in paths)+']')
PY
)"
gsettings set "$SCHEMA" custom-list "$CUSTOM_LIST"
for item in "custom0|<Super>space" "custom1|<Alt>space"; do
  IFS='|' read -r name binding <<< "$item"
  CUSTOM="org.cinnamon.desktop.keybindings.custom-keybinding:$BASE/$name/"
  gsettings set "$CUSTOM" name 'Toggle Jarvis HUD'
  gsettings set "$CUSTOM" command "$HOME/.local/bin/jarvis"
  gsettings set "$CUSTOM" binding "$binding"
done

systemctl --user daemon-reload
if [[ -n "${OPENAI_API_KEY:-}" ]]; then systemctl --user import-environment OPENAI_API_KEY; fi
if grep -q '^JARVIS_LOCAL_URL=http://127\.0\.0\.1:11434/' "$HOME/.config/jarvis/local.env" 2>/dev/null; then
  systemctl --user disable --now jarvis-llm.service >/dev/null 2>&1 || true
  systemctl --user enable --now jarvis.service
else
  systemctl --user enable --now jarvis-llm.service jarvis.service
fi
echo "Jarvis enabled with Cinnamon Super+Space and Alt+Space shortcuts."

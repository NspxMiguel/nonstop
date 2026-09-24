#!/usr/bin/env bash
# Remove nonstop. Keychain secrets and ~/.claude/nonstop (state, log) are kept.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SHARE="${NONSTOP_PREFIX:-$HOME/.local/share/nonstop}"

python3 - "$CLAUDE_DIR/settings.json" <<'PY'
import json, sys
path = sys.argv[1]
try:
    settings = json.load(open(path))
except FileNotFoundError:
    sys.exit(0)
hooks = settings.get("hooks", {})
groups = []
for group in hooks.get("Stop", []):
    kept = [h for h in group.get("hooks", []) if "nonstop" not in h.get("command", "")]
    if kept:
        groups.append({**group, "hooks": kept})
if groups:
    hooks["Stop"] = groups
else:
    hooks.pop("Stop", None)
with open(path, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

rm -f "$HOME/.local/bin/nonstop"
rm -rf "$SHARE"
for skill in autonomous tarefas senhas; do rm -rf "$CLAUDE_DIR/skills/$skill"; done
echo "nonstop removed (secrets in the keychain and ~/.claude/nonstop were kept)."

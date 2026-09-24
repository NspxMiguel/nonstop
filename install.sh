#!/usr/bin/env bash
# Install nonstop: the CLI, the Stop hook and the /autonomous, /tarefas, /senhas skills.
# Safe to run again; it replaces its own files and hook entry and leaves everything else alone.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SHARE="${NONSTOP_PREFIX:-$HOME/.local/share/nonstop}"
BIN="$HOME/.local/bin"

command -v python3 >/dev/null || {
  echo "python3 is required" >&2
  exit 1
}

mkdir -p "$SHARE/tools" "$BIN" "$CLAUDE_DIR/skills"
install -m 755 "$SRC/nonstop.py" "$SHARE/nonstop.py"
install -m 755 "$SRC/tools/vault.py" "$SRC/tools/harvest.py" "$SHARE/tools/"
ln -sf "$SHARE/nonstop.py" "$BIN/nonstop"

for skill in autonomous tarefas senhas; do
  rm -rf "$CLAUDE_DIR/skills/$skill"
  cp -R "$SRC/skills/$skill" "$CLAUDE_DIR/skills/$skill"
done

# Leftovers of the first, always-on version.
rm -f "$CLAUDE_DIR/hooks/nonstop_stop.py"
rm -rf "$CLAUDE_DIR/skills/nonstop" "$CLAUDE_DIR/nonstop/state"

python3 - "$CLAUDE_DIR/settings.json" "$SHARE/nonstop.py" <<'PY'
import json, os, sys
path, script = sys.argv[1], sys.argv[2]
try:
    settings = json.load(open(path))
except FileNotFoundError:
    settings = {}
if os.path.exists(path):
    open(path + ".bak-nonstop", "w").write(open(path).read())
hooks = settings.setdefault("hooks", {})
ours = lambda h: "nonstop" in h.get("command", "")
groups = []
for group in hooks.get("Stop", []):
    kept = [h for h in group.get("hooks", []) if not ours(h)]
    if kept:
        groups.append({**group, "hooks": kept})
groups.append({"hooks": [{"type": "command", "command": f'python3 "{script}" hook', "timeout": 10}]})
hooks["Stop"] = groups
with open(path, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

echo "nonstop installed:"
echo "  CLI     $BIN/nonstop  ($("$BIN/nonstop" --version))"
echo "  hook    Stop -> $SHARE/nonstop.py hook"
echo "  skills  /autonomous  /tarefas  /senhas"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  note: add $BIN to your PATH" ;; esac

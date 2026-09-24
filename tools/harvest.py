#!/usr/bin/env python3
"""Find credentials already on this machine and put them in the keychain.

The point is that nobody types anything. Keys tend to already exist — in .env
files, in shell profiles, in the config a CLI wrote when it was set up. This
walks the likely places, and hands each value to `nonstop secret
import` so it lands in the OS keychain where `run` can inject it.

Values are never printed. Every report line is a name, a length and a source.

    nonstop harvest              # show what would be imported
    nonstop harvest --apply      # import it
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLI = os.environ.get("NONSTOP_CLI") or str(Path(__file__).resolve().parent.parent / "nonstop.py")

HOME = Path.home()

# Where credentials habitually sit. Directories are walked one level deep for
# .env files; explicit files are parsed directly.
ENV_DIRS = [
    HOME / "Documents",
    HOME / "Projects",
    HOME / "Developer",
    HOME / "src",
    HOME / "code",
    HOME / "dev",
]
ENV_NAMES = {".env", ".env.local", ".env.development", ".env.production"}
SHELL_FILES = [HOME / n for n in (".zshrc", ".bashrc", ".bash_profile", ".profile", ".zshenv")]

# Config files a CLI wrote, mapped to the JSON path holding the secret.
JSON_SOURCES: list[tuple[Path, list[str], str]] = [
    (HOME / ".config/gh/hosts.yml", [], "GITHUB_TOKEN"),  # yaml, handled separately
    (HOME / ".openai/config.json", ["api_key"], "OPENAI_API_KEY"),
    (HOME / ".config/anthropic/config.json", ["api_key"], "ANTHROPIC_API_KEY"),
]

# A name only counts as a secret if it looks like one.
SECRET_NAME = re.compile(
    r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|ACCESS[_-]?KEY|"
    r"PRIVATE[_-]?KEY|CLIENT[_-]?SECRET|WEBHOOK|DSN|AUTH)",
    re.IGNORECASE,
)
# Names that end in a public identifier are not credentials, however much the
# rest of the name looks like one: AUTH_DOMAIN is a hostname, CLIENT_ID is
# public by design, and PROJECT_ID ships in every frontend bundle.
NOT_SECRET_NAME = re.compile(
    r"(_DOMAIN|_URL|_URI|_HOST|_ENDPOINT|_ID|_BUCKET|_REGION|_PROJECT|"
    r"_SENDER_ID|_APP_ID|_MEASUREMENT_ID|_PUBLIC[_-]?KEY|_PUBLISHABLE[_-]?KEY)$",
    re.IGNORECASE,
)
# And obvious non-secrets are dropped even when the name matches.
PLACEHOLDER = re.compile(
    r"^(|x+|your[_-]?\w*|changeme|todo|none|null|undefined|placeholder|"
    r"<[^>]*>|\$\{?\w+\}?|\*+|\.{3,})$",
    re.IGNORECASE,
)
# A value that is plainly an address or a path is configuration, not a secret.
LOOKS_PUBLIC = re.compile(r"^(https?://|[\w.-]+\.(com|net|org|dev|io|app|cloud)(/|$)|/|\./)", re.IGNORECASE)
MIN_LEN = 12

ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def looks_secret(name: str, value: str) -> bool:
    if not SECRET_NAME.search(name):
        return False
    if NOT_SECRET_NAME.search(name):
        return False
    if LOOKS_PUBLIC.match(value):
        return False
    if len(value) < MIN_LEN:
        return False
    if PLACEHOLDER.match(value):
        return False
    # A command or a reference, not a value.
    return not ("$(" in value or value.startswith("$"))


def scan_assignments(path: Path, source: str) -> list[tuple[str, str, str]]:
    out = []
    try:
        text = path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = ASSIGN.match(line)
        if not m:
            continue
        name, value = m.group(1), unquote(m.group(2))
        if value.endswith("\\"):  # line continuation, skip rather than mangle
            continue
        if looks_secret(name, value):
            out.append((name, value, source))
    return out


def scan_json(path: Path, keys: list[str], name: str) -> list[tuple[str, str, str]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    node = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return []
        node = node[k]
    if isinstance(node, str) and looks_secret(name, node):
        return [(name, node, str(path).replace(str(HOME), "~"))]
    return []


def scan_gh_hosts() -> list[tuple[str, str, str]]:
    p = HOME / ".config/gh/hosts.yml"
    try:
        text = p.read_text()
    except OSError:
        return []
    m = re.search(r"^\s*oauth_token:\s*(\S+)", text, re.MULTILINE)
    if m and looks_secret("GITHUB_TOKEN", m.group(1)):
        return [("GITHUB_TOKEN", m.group(1), "~/.config/gh/hosts.yml")]
    return []


def collect() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []

    for f in SHELL_FILES:
        if f.is_file():
            found += scan_assignments(f, str(f).replace(str(HOME), "~"))

    seen_dirs = 0
    for root in ENV_DIRS:
        if not root.is_dir():
            continue
        for path in root.rglob(".env*"):
            if path.name not in ENV_NAMES or not path.is_file():
                continue
            # Skip anything inside a dependency or build directory.
            if any(
                part in {"node_modules", ".git", "vendor", "dist", "build", ".next", "target", "venv", ".venv"}
                for part in path.parts
            ):
                continue
            seen_dirs += 1
            if seen_dirs > 400:
                break
            found += scan_assignments(path, str(path).replace(str(HOME), "~"))

    found += scan_gh_hosts()
    for path, keys, name in JSON_SOURCES:
        if keys and path.is_file():
            found += scan_json(path, keys, name)

    # Same name from several files: keep the longest value, which is almost
    # always the real key rather than a truncated sample.
    best: dict[str, tuple[str, str, str]] = {}
    for name, value, source in found:
        prev = best.get(name)
        if prev is None or len(value) > len(prev[1]):
            best[name] = (name, value, source)
    return sorted(best.values())


PT = (os.environ.get("NONSTOP_LANG") or os.environ.get("LANG") or "").lower().startswith("pt")
MSG = {
    "nothing": (
        "nada encontrado nos lugares habituais (.env, perfis de shell, config de CLI)",
        "nothing found in the usual places (.env, shell profiles, CLI configs)",
    ),
    "found": ("{n} credencial(is) encontrada(s):\n", "{n} credential(s) found:\n"),
    "new": ("novo", "new"),
    "stored": ("ja no chaveiro", "in keychain"),
    "to_import": (
        "\n{n} novo(s). Para importar:  nonstop harvest --apply",
        "\n{n} new. To import:  nonstop harvest --apply",
    ),
    "none_new": ("\nnada novo para importar", "\nnothing new to import"),
    "failed": ("  falhou {name}: {err}", "  failed {name}: {err}"),
    "imported": ("{ok}/{n} importada(s) para o chaveiro", "{ok}/{n} imported into the keychain"),
    "use": ("use com:  nonstop run NOME -- seu-comando", "use with:  nonstop run NAME -- your-command"),
}


def msg(key: str, **fmt) -> str:
    return MSG[key][0 if PT else 1].format(**fmt)


def already_stored() -> set[str]:
    p = subprocess.run([CLI, "secret", "list"], capture_output=True, check=False, text=True)
    return {ln.strip() for ln in p.stdout.splitlines() if ln.startswith("  ") and ln.strip()}


def main() -> int:
    apply = "--apply" in sys.argv
    items = collect()
    if not items:
        print(msg("nothing"))
        return 0

    have = already_stored()
    new = [i for i in items if i[0] not in have]

    width = max(len(n) for n, _, _ in items)
    print(msg("found", n=len(items)))
    for name, value, source in items:
        mark = msg("stored") if name in have else msg("new")
        print(f"  {name.ljust(width)}  {len(value):>4} chars  {mark:<14} {source}")

    if not apply:
        print(msg("to_import", n=len(new)))
        return 0

    if not new:
        print(msg("none_new"))
        return 0

    ok = 0
    print()
    for name, value, _ in new:
        p = subprocess.run([CLI, "secret", "import", name], input=value, capture_output=True, check=False, text=True)
        if p.returncode == 0:
            ok += 1
        else:
            err = ((p.stdout + p.stderr).strip().splitlines() or ["?"])[0]
            print(msg("failed", name=name, err=err))
    print(msg("imported", ok=ok, n=len(new)))
    print(msg("use"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

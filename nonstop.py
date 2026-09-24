#!/usr/bin/env python3
"""nonstop — keep a Claude Code session working until the user says "chega".

Off by default. The user turns it on with /autonomous; from then on a Stop hook
refuses to let the session end its turn while the task list still has open
items, and makes it sweep for loose ends once the list is empty. "chega",
"/autonomous off" or `nonstop off` end it.

One file, two roles:
  nonstop <command>        CLI for the user and for Claude (tasks, on/off, status)
  nonstop hook             the Stop hook (reads the hook JSON on stdin)

State lives in ~/.claude/nonstop/sessions/<session_id>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2.0.0"

HOME_DIR = Path(os.environ.get("NONSTOP_HOME") or Path.home() / ".claude" / "nonstop")
SESSIONS_DIR = HOME_DIR / "sessions"
LOG_FILE = HOME_DIR / "log.jsonl"
LANG_FILE = HOME_DIR / "lang"

MAX_BLOCKS_PER_PROMPT = int(os.environ.get("NONSTOP_MAX_BLOCKS", "300"))
MAX_IDLE_BLOCKS = 3  # blocks in a row with no tool call in between
MAX_HOURS = float(os.environ.get("NONSTOP_MAX_HOURS", "12"))
STATE_TTL_DAYS = 7
SHORT_PROMPT = 40  # a stop word only counts in a message this short

STOP_WORDS = re.compile(r"\b(chega|para tudo|pode parar|stop now|enough)\b", re.IGNORECASE)
CMD_RE = re.compile(
    r"<command-name>/?autonomous</command-name>(?:\s*<command-args>(.*?)</command-args>)?",
    re.DOTALL,
)
HOOK_TAG = "[nonstop]"
DONE_MARKERS = ("[NONSTOP:DONE]", "[NONSTOP:BLOCKED]")

MARKS = {"open": " ", "doing": "~", "done": "x", "blocked": "!"}

# ------------------------------------------------------------------ i18n

MESSAGES = {
    "on": {"pt": "Modo nonstop LIGADO nesta sessão.", "en": "Nonstop mode ON for this session."},
    "off": {"pt": "Modo nonstop DESLIGADO nesta sessão.", "en": "Nonstop mode OFF for this session."},
    "status_on": {"pt": "ligado", "en": "on"},
    "status_off": {"pt": "desligado", "en": "off"},
    "mode": {"pt": "Modo", "en": "Mode"},
    "task": {"pt": "Pedido", "en": "Request"},
    "since": {"pt": "desde", "en": "since"},
    "no_tasks": {"pt": "Nenhuma tarefa ainda.", "en": "No tasks yet."},
    "added": {"pt": "Adicionada", "en": "Added"},
    "need_proof": {
        "pt": 'Tarefa só fecha com prova: --proof "comando e o que saiu".',
        "en": 'A task only closes with proof: --proof "command and what it printed".',
    },
    "need_reason": {"pt": 'Diga por que travou: --reason "...".', "en": 'Say why it is blocked: --reason "...".'},
    "bad_index": {"pt": "Não existe tarefa", "en": "No task number"},
    "no_session": {
        "pt": "Sem sessão: rode dentro do Claude Code ou passe --session <id>.",
        "en": "No session: run inside Claude Code or pass --session <id>.",
    },
    "lang_saved": {"pt": "Idioma salvo", "en": "Language saved"},
    "bad_name": {
        "pt": "Nome inválido: {name} (use LETRAS_E_UNDERLINE).",
        "en": "Invalid name: {name} (use LETTERS_AND_UNDERSCORES).",
    },
    "mac_only": {"pt": "O cofre usa o chaveiro do macOS.", "en": "The vault uses the macOS keychain."},
    "secrets_header": {"pt": "{n} segredo(s) em {prefix}:", "en": "{n} secret(s) under {prefix}:"},
    "empty_value": {
        "pt": "Valor vazio — mande pelo stdin ou use --clipboard.",
        "en": "Empty value — pipe it on stdin or use --clipboard.",
    },
    "store_failed": {"pt": "Falhou ao gravar {name} no chaveiro.", "en": "Could not store {name} in the keychain."},
    "stored": {"pt": "Guardado {name} ({n} caracteres).", "en": "Stored {name} ({n} characters)."},
    "removed": {"pt": "Removido {name}.", "en": "Removed {name}."},
    "not_stored": {
        "pt": "Não guardado: {names}  (adicione com: nonstop secret set NOME)",
        "en": "Not stored: {names}  (add with: nonstop secret set NAME)",
    },
    "summary": {
        "pt": "{open} abertas, {done} feitas, {blocked} travadas",
        "en": "{open} open, {done} done, {blocked} blocked",
    },
}


def lang() -> str:
    """Environment wins, then the saved choice, then the system locale."""
    forced = (os.environ.get("NONSTOP_LANG") or "").lower()[:2]
    if forced in ("pt", "en"):
        return forced
    try:
        saved = LANG_FILE.read_text().strip().lower()[:2]
        if saved in ("pt", "en"):
            return saved
    except OSError:
        pass
    env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "pt" if env.lower().startswith("pt") else "en"


def t(key: str, **fmt) -> str:
    return MESSAGES[key][lang()].format(**fmt)


# ------------------------------------------------------------------ state


def now() -> float:
    return time.time()


def state_path(session: str) -> Path:
    return SESSIONS_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', session)}.json"


def load(session: str) -> dict:
    try:
        return json.loads(state_path(session).read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": False, "tasks": []}


def save(session: str, state: dict) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(session)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(path)


def prune_old_states() -> None:
    if not SESSIONS_DIR.exists():
        return
    for old in SESSIONS_DIR.glob("*.json"):
        try:
            if now() - old.stat().st_mtime > STATE_TTL_DAYS * 86400:
                old.unlink()
        except OSError:
            pass


def log(event: dict) -> None:
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def turn_on(state: dict, task: str | None = None) -> None:
    state["active"] = True
    state["since"] = now()
    state.setdefault("tasks", [])
    if task:
        state["request"] = task
    state.pop("turn", None)


def turn_off(state: dict) -> None:
    state["active"] = False
    state.pop("turn", None)


def open_tasks(state: dict) -> list[tuple[int, dict]]:
    return [(i + 1, x) for i, x in enumerate(state.get("tasks", [])) if x["status"] in ("open", "doing")]


def render_tasks(state: dict) -> str:
    tasks = state.get("tasks", [])
    if not tasks:
        return t("no_tasks")
    lines = []
    for i, x in enumerate(tasks, 1):
        line = f"{i}. [{MARKS[x['status']]}] {x['text']}"
        if x.get("proof"):
            line += f"  — proof: {x['proof']}"
        if x.get("reason"):
            line += f"  — blocked: {x['reason']}"
        lines.append(line)
    return "\n".join(lines)


def summary(state: dict) -> str:
    tasks = state.get("tasks", [])
    count = lambda *s: sum(1 for x in tasks if x["status"] in s)
    return t("summary", open=count("open", "doing"), done=count("done"), blocked=count("blocked"))


# ------------------------------------------------------------------ transcript


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_real_prompt(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    text = text_of(content).strip()
    return bool(text) and HOOK_TAG not in text and not text.startswith("Stop hook feedback")


def control_of(text: str) -> str | None:
    """Return 'on' / 'off' when a prompt is a mode switch, else None."""
    m = CMD_RE.search(text)
    if m:
        args = (m.group(1) or "").strip().lower()
        return "off" if args in ("off", "stop", "desliga", "chega") else "on"
    stripped = text.strip()
    if stripped.lower().startswith("/autonomous"):
        return "off" if re.search(r"\b(off|stop|desliga)\b", stripped.lower()) else "on"
    if len(stripped) <= SHORT_PROMPT and STOP_WORDS.search(stripped):
        return "off"
    return None


def read_transcript(path: str) -> dict:
    """Summarise the transcript: latest control switch, current turn, tool count."""
    out = {"control": None, "control_id": None, "prompt_id": None, "prompt": "", "tools": 0, "last_text": ""}
    entries = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return out
    start = None
    for i, e in enumerate(entries):
        if not is_real_prompt(e):
            continue
        start = i
        text = text_of(e.get("message", {}).get("content"))
        ctl = control_of(text)
        if ctl:
            out["control"], out["control_id"] = ctl, e.get("uuid") or str(i)
    if start is None:
        return out
    prompt = entries[start]
    out["prompt_id"] = prompt.get("uuid") or str(start)
    out["prompt"] = text_of(prompt.get("message", {}).get("content"))
    for e in entries[start + 1 :]:
        if e.get("type") != "assistant":
            continue
        content = e.get("message", {}).get("content")
        if isinstance(content, list):
            out["tools"] += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
        txt = text_of(content).strip()
        if txt:
            out["last_text"] = txt
    return out


# ------------------------------------------------------------------ hook

OPEN_REASON = """{tag} Autonomous mode is on and the task list still has open items:

{tasks}

Keep working: take the first open item (`nonstop doing <n>`), do it, prove it, and close it with `nonstop done <n> --proof "command and what it printed"`. Work you discover goes in with `nonstop add "..."`. If an item depends only on the user (password, 6-digit code, money, a real decision), mark it with `nonstop blocked <n> --reason "..."` and move to the next one. The user ends this by saying "chega"."""

SWEEP_REASON = """{tag} The task list is empty — one sweep before you stop:

1. Re-read the original request{request}. Anything asked that never became a task? Add it and do it.
2. The literal ask is the floor: did you test it for real, check the neighbouring flows, and look for the same bug elsewhere?
3. Loose ends: TODOs you left, failing/unrun tests, lint/type errors, uncommitted or unpushed work, stale README/notes, dev servers or simulators still running.

Found something → `nonstop add "..."` and keep going. Nothing left → end with [NONSTOP:DONE] and a short evidence list (or [NONSTOP:BLOCKED] listing what only the user can do)."""


def hook(data: dict) -> dict | None:
    session = data.get("session_id") or "unknown"
    prune_old_states()
    info = read_transcript(data.get("transcript_path", ""))
    state = load(session)

    # Apply a mode switch typed by the user that we have not seen yet.
    if info["control_id"] and info["control_id"] != state.get("control_id"):
        state["control_id"] = info["control_id"]
        (turn_on if info["control"] == "on" else turn_off)(state)
        save(session, state)

    if not state.get("active"):
        return None
    if now() - state.get("since", now()) > MAX_HOURS * 3600:
        turn_off(state)
        save(session, state)
        log({"session": session, "decision": "allow", "why": "max hours"})
        return None

    turn = state.get("turn") or {}
    if turn.get("prompt") != info["prompt_id"]:
        turn = {"prompt": info["prompt_id"], "blocks": 0, "idle": 0, "tools_at_block": -1, "swept": False}
    pending = open_tasks(state)

    if not pending and info["tools"] == 0 and not turn["blocks"]:
        return None  # plain conversation with nothing on the list
    if not pending and turn["swept"] and any(m in info["last_text"] for m in DONE_MARKERS):
        state.pop("turn", None)
        save(session, state)
        log({"session": session, "decision": "allow", "why": "swept"})
        return None

    turn["idle"] = turn["idle"] + 1 if info["tools"] == turn["tools_at_block"] else 0
    if turn["blocks"] >= MAX_BLOCKS_PER_PROMPT or turn["idle"] >= MAX_IDLE_BLOCKS:
        state.pop("turn", None)
        save(session, state)
        log({"session": session, "decision": "allow", "why": "safety valve", "blocks": turn["blocks"]})
        return None

    turn["blocks"] += 1
    turn["tools_at_block"] = info["tools"]
    if pending:
        listing = "\n".join(f"{n}. [{MARKS[x['status']]}] {x['text']}" for n, x in pending)
        reason = OPEN_REASON.format(tag=HOOK_TAG, tasks=listing)
    else:
        turn["swept"] = True
        request = f' ("{state["request"]}")' if state.get("request") else ""
        reason = SWEEP_REASON.format(tag=HOOK_TAG, request=request)
    state["turn"] = turn
    save(session, state)
    log({"session": session, "decision": "block", "open": len(pending), "blocks": turn["blocks"]})
    return {"decision": "block", "reason": reason}


# ------------------------------------------------------------------ secrets
#
# API keys and tokens live in the macOS keychain under "<prefix>:<NAME>" (the
# prefix stays "claude-autonomous" so keys saved by the old tool keep working).
# Values never go through argv or stdout: `set`/`import` read stdin, `run`
# exports them into the child's environment, `list` prints names only.

SECRET_PREFIX = os.environ.get("NONSTOP_SECRET_PREFIX", "claude-autonomous")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOOLS_DIR = Path(__file__).resolve().parent / "tools"


def _security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["security", *args], input=stdin, capture_output=True, check=False, text=True)


def secret_store(name: str, value: str) -> bool:
    # `security -i` reads the command from stdin, so the value never shows up in `ps`.
    quoted = value.replace("\\", "\\\\").replace('"', '\\"')
    user = os.environ.get("USER", "")
    cmd = f'add-generic-password -U -a "{user}" -s "{SECRET_PREFIX}:{name}" -D "nonstop secret" -w "{quoted}"\n'
    _security("-i", stdin=cmd)
    return secret_get(name) == value


def secret_get(name: str) -> str | None:
    p = _security("find-generic-password", "-a", os.environ.get("USER", ""), "-s", f"{SECRET_PREFIX}:{name}", "-w")
    return p.stdout.rstrip("\n") if p.returncode == 0 else None


def secret_names() -> list[str]:
    out = _security("dump-keychain").stdout
    return sorted(set(re.findall(r'"svce"<blob>="' + re.escape(SECRET_PREFIX) + r':([^"]+)"', out)))


def secret_rm(name: str) -> bool:
    return (
        _security(
            "delete-generic-password", "-a", os.environ.get("USER", ""), "-s", f"{SECRET_PREFIX}:{name}"
        ).returncode
        == 0
    )


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        sys.exit(t("bad_name", name=name))


def cmd_secret(args) -> int:
    if sys.platform != "darwin":
        sys.exit(t("mac_only"))
    op = args.op
    if op == "list":
        names = secret_names()
        print(t("secrets_header", n=len(names), prefix=SECRET_PREFIX))
        for n in names:
            print(f"  {n}")
        return 0
    check_name(args.name)
    if op in ("set", "import"):
        if getattr(args, "clipboard", False):
            value = subprocess.run(["pbpaste"], capture_output=True, check=False, text=True).stdout
        else:
            value = sys.stdin.read()
        value = value.strip()
        if not value:
            sys.exit(t("empty_value"))
        if not secret_store(args.name, value):
            sys.exit(t("store_failed", name=args.name))
        print(t("stored", name=args.name, n=len(value)))
        return 0
    if op == "rm":
        if not secret_rm(args.name):
            sys.exit(t("not_stored", names=args.name))
        print(t("removed", name=args.name))
        return 0
    if op == "has":
        return 0 if secret_get(args.name) else 1
    return 2


def cmd_run(names: str, command: list[str]) -> int:
    if command[:1] == ["--"]:
        command = command[1:]
    if not names or not command:
        sys.exit("usage: nonstop run NAME[,NAME] -- command [args...]")
    env = dict(os.environ)
    missing = []
    for name in names.split(","):
        check_name(name)
        value = secret_get(name)
        if value:
            env[name] = value
        else:
            missing.append(name)
    if missing:
        sys.exit(t("not_stored", names=", ".join(missing)))
    os.execvpe(command[0], command, env)


def run_tool(script: str, extra: list[str]) -> int:
    env = dict(os.environ, NONSTOP_CLI=str(Path(__file__).resolve()), NONSTOP_LANG=lang())
    if script == "vault.py":
        extra = [*extra, "--lang", lang()]
    return subprocess.call([sys.executable, str(TOOLS_DIR / script), *extra], env=env)


# ------------------------------------------------------------------ CLI


def session_id(args) -> str:
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        sys.exit(t("no_session"))
    return sid


def task_at(state: dict, n: int) -> dict:
    tasks = state.get("tasks", [])
    if not 1 <= n <= len(tasks):
        sys.exit(f"{t('bad_index')} {n}.")
    return tasks[n - 1]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        try:
            data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            return 0
        result = hook(data)
        if result:
            print(json.dumps(result, ensure_ascii=False))
        return 0

    p = argparse.ArgumentParser(prog="nonstop", description="Keep a Claude Code session working until you say 'chega'.")
    p.add_argument("--session", help="session id (default: $CLAUDE_CODE_SESSION_ID)")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd")
    on = sub.add_parser("on", help="turn nonstop on for this session")
    on.add_argument("--task", help="the original request, in the user's words")
    sub.add_parser("off", help="turn nonstop off for this session")
    sub.add_parser("status", help="mode and task summary")
    add = sub.add_parser("add", help="add one or more tasks")
    add.add_argument("items", nargs="+")
    sub.add_parser("list", aliases=["tasks", "tarefas"], help="show the task list")
    doing = sub.add_parser("doing", help="mark a task as in progress")
    doing.add_argument("n", type=int)
    done = sub.add_parser("done", help="close a task (proof required)")
    done.add_argument("n", type=int)
    done.add_argument("--proof", default="")
    blocked = sub.add_parser("blocked", help="park a task that only the user can unblock")
    blocked.add_argument("n", type=int)
    blocked.add_argument("--reason", default="")
    reopen = sub.add_parser("reopen", help="put a task back to open")
    reopen.add_argument("n", type=int)
    sub.add_parser("clear", help="drop every task of this session")
    sec = sub.add_parser("secret", aliases=["senhas", "senha"], help="API keys in the macOS keychain")
    sec_sub = sec.add_subparsers(dest="op", required=True)
    sec_sub.add_parser("list", help="names only, never values")
    for op, hlp in (("set", "store NAME from stdin (or --clipboard)"), ("import", "same as set, for scripts")):
        sp = sec_sub.add_parser(op, help=hlp)
        sp.add_argument("name")
        sp.add_argument("--clipboard", action="store_true")
    for op, hlp in (("rm", "delete NAME"), ("has", "exit 0 when NAME is stored")):
        sec_sub.add_parser(op, help=hlp).add_argument("name")
    run = sub.add_parser("run", help="run a command with secrets in its environment")
    run.add_argument("names", help="NAME[,NAME...]")
    run.add_argument("command", nargs=argparse.REMAINDER)
    vault = sub.add_parser("vault", aliases=["cofre"], help="local page to paste keys or a whole .env")
    vault.add_argument("rest", nargs=argparse.REMAINDER)
    req = sub.add_parser("request", help="ask the user for one secret through the vault page")
    req.add_argument("name")
    req.add_argument("reason", nargs="?", default="")
    req.add_argument("--no-open", action="store_true", help="print the URL instead of opening the browser")
    harvest = sub.add_parser("harvest", help="find keys already on this machine (.env, shell profiles, CLI configs)")
    harvest.add_argument("--apply", action="store_true")
    lng = sub.add_parser("lang", help="save the CLI language (pt|en)")
    lng.add_argument("code", choices=["pt", "en"])
    args = p.parse_args(argv)

    if args.cmd == "lang":
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        LANG_FILE.write_text(args.code)
        print(f"{t('lang_saved')}: {args.code}")
        return 0
    if args.cmd in ("secret", "senhas", "senha"):
        return cmd_secret(args)
    if args.cmd == "run":
        return cmd_run(args.names, args.command)
    if args.cmd in ("vault", "cofre"):
        return run_tool("vault.py", args.rest)
    if args.cmd == "request":
        check_name(args.name)
        extra = ["--need", args.name, "--reason", args.reason] + (["--no-open"] if args.no_open else [])
        return run_tool("vault.py", extra)
    if args.cmd == "harvest":
        return run_tool("harvest.py", ["--apply"] if args.apply else [])
    if args.cmd is None:
        p.print_help()
        return 0

    sid = session_id(args)
    state = load(sid)
    cmd = args.cmd
    if cmd == "on":
        turn_on(state, args.task)
        print(t("on"))
    elif cmd == "off":
        turn_off(state)
        print(t("off"))
    elif cmd == "status":
        mode = t("status_on") if state.get("active") else t("status_off")
        line = f"{t('mode')}: {mode}"
        if state.get("active") and state.get("since"):
            line += f" ({t('since')} {time.strftime('%H:%M', time.localtime(state['since']))})"
        print(line)
        if state.get("request"):
            print(f"{t('task')}: {state['request']}")
        print(summary(state))
    elif cmd == "add":
        for item in args.items:
            state.setdefault("tasks", []).append({"text": item, "status": "open"})
            print(f"{t('added')} #{len(state['tasks'])}: {item}")
    elif cmd in ("list", "tasks", "tarefas"):
        print(render_tasks(state))
        print(summary(state))
    elif cmd == "doing":
        task_at(state, args.n)["status"] = "doing"
    elif cmd == "done":
        if not args.proof.strip():
            sys.exit(t("need_proof"))
        x = task_at(state, args.n)
        x.update(status="done", proof=args.proof.strip())
        x.pop("reason", None)
    elif cmd == "blocked":
        if not args.reason.strip():
            sys.exit(t("need_reason"))
        task_at(state, args.n).update(status="blocked", reason=args.reason.strip())
    elif cmd == "reopen":
        x = task_at(state, args.n)
        x["status"] = "open"
        x.pop("proof", None)
        x.pop("reason", None)
    elif cmd == "clear":
        state["tasks"] = []
    save(sid, state)
    if cmd in ("doing", "done", "blocked", "reopen"):
        print(render_tasks(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())

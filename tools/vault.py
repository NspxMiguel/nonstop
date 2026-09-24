#!/usr/bin/env python3
"""Local vault UI for nonstop.

A one-page app on 127.0.0.1 where you paste a secret — or a whole .env — and it
goes straight into the OS keychain. The agent then uses it through
`nonstop run NAME -- ...` without the value ever passing through a
conversation.

What this deliberately is not: it does not hold account passwords, drive login
forms, or authenticate as anyone. It stores API keys and tokens you already
have, so that handing one over stops being a terminal command you must recall.

Stdlib only. Binds loopback, requires a per-run token, validates the Host
header, and exits on idle.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser

CLI = os.environ.get("NONSTOP_CLI") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nonstop.py"
)
TOKEN = secrets.token_urlsafe(24)
IDLE_TIMEOUT = 15 * 60
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_last_seen = time.time()
_lock = threading.Lock()


def touch() -> None:
    global _last_seen
    with _lock:
        _last_seen = time.time()


def cli(*args: str, stdin: str | None = None) -> tuple[int, str]:
    """Run the CLI. Values travel on stdin, never in argv."""
    p = subprocess.run(
        [CLI, *args],
        input=stdin,
        capture_output=True,
        check=False,
        text=True,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


PROTON_VAULT = os.environ.get("CLAUDE_AUTONOMOUS_PROTON_VAULT") or ""


def proton_session() -> bool:
    try:
        return subprocess.run(["pass-cli", "info"], capture_output=True, check=False).returncode == 0
    except FileNotFoundError:
        return False


def proton_store(name: str, value: str) -> bool:
    """Best-effort write to Proton Pass as a login item, value as password on
    stdin (never argv). Only runs when a session exists."""
    if not proton_session():
        return False
    tmpl = {"title": name, "content": {"username": name, "password": value, "urls": []}}
    cmd = ["pass-cli", "item", "create", "login", "--from-template", "-"]
    if PROTON_VAULT:
        cmd[3:3] = ["--vault-name", PROTON_VAULT]
    try:
        p = subprocess.run(cmd, input=json.dumps(tmpl), capture_output=True, check=False, text=True)
        return p.returncode == 0
    except FileNotFoundError:
        return False


def store(name: str, value: str) -> tuple[bool, dict]:
    """Returns (ok, message) where message is {key, ...params}; the page translates it."""
    if not NAME_RE.match(name):
        return False, {"key": "bad_name"}
    if not value.strip():
        return False, {"key": "empty"}
    value = value.strip()
    code, _ = cli("secret", "import", name, stdin=value)
    if code != 0:
        return False, {"key": "store_failed", "name": name}  # never echo the value
    where = ["keychain"]
    if proton_store(name, value):
        where.append("Proton")
    # macOS Passwords app cannot be written from a CLI, so it is never a target.
    return True, {"key": "stored", "name": name, "n": len(value), "where": where}


def parse_env(text: str) -> list[tuple[str, str]]:
    """Accept .env, `export A=b`, and `A: b` shapes. Values are never logged."""
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)$", line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            out.append((name, value))
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vault — nonstop</title>
<style>
:root{--bg:#16150f;--fg:#eae6dd;--muted:#948d80;--line:#2e2b23;--card:#1d1c15;
      --accent:#e08a4e;--ok:#6fbf73;--bad:#e0625e;--code:#22201a}
@media(prefers-color-scheme:light){:root{--bg:#fbfaf8;--fg:#1a1815;--muted:#6b655c;
      --line:#e2ded6;--card:#fff;--accent:#b4521f;--code:#f2efe9}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:44rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:1rem}
h1{font-size:1.6rem;margin:0 0 .3rem;letter-spacing:-.02em}
.sub{color:var(--muted);margin:0 0 2rem}
h2{font-size:1rem;margin:2rem 0 .6rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.1rem}
label{display:block;font-size:.85rem;color:var(--muted);margin:0 0 .3rem}
input,textarea{width:100%;background:var(--code);color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:.6rem .7rem;font:14px ui-monospace,SFMono-Regular,Menlo,monospace}
textarea{min-height:8.5rem;resize:vertical}
.row{display:flex;gap:.6rem;margin-top:.7rem;align-items:center;flex-wrap:wrap}
button{background:var(--accent);color:#fff;border:0;border-radius:7px;
  padding:.55rem 1rem;font:600 14px system-ui;cursor:pointer}
button.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
.msg{margin-top:.7rem;font-size:.9rem;min-height:1.3em}
.ok{color:var(--ok)} .bad{color:var(--bad)}
ul{list-style:none;padding:0;margin:.4rem 0 0}
li{display:flex;justify-content:space-between;align-items:center;
   padding:.5rem .1rem;border-bottom:1px solid var(--line);font-family:ui-monospace,Menlo,monospace;font-size:14px}
li:last-child{border-bottom:0}
li button{background:transparent;color:var(--muted);border:1px solid var(--line);padding:.2rem .55rem;font-size:12px}
.hint{color:var(--muted);font-size:.85rem;margin-top:.5rem}
.ask{background:rgba(224,138,78,.12);border:1px solid var(--accent);border-radius:9px;
     padding:.8rem 1rem;margin:0 0 1.2rem;font-size:.95rem}
.ask b{color:var(--accent)}
code{background:var(--code);padding:.1em .35em;border-radius:4px;font-size:.9em}
</style></head><body><div class="wrap">

<div class="top"><h1 data-i="title"></h1>
  <button class="ghost" id="lang" type="button" aria-label="Language"></button></div>
<div id="ask" class="ask" hidden></div>
<p class="sub" data-i="sub"></p>

<h2 data-i="one"></h2>
<div class="card">
  <label for="n" data-i="name"></label>
  <input id="n" placeholder="GROQ_API_KEY" autocomplete="off" spellcheck="false">
  <div style="margin-top:.7rem"></div>
  <label for="v" data-i="value"></label>
  <input id="v" type="password" autocomplete="off">
  <div class="row">
    <button id="save" data-i="save"></button>
    <button class="ghost" id="peek" type="button" data-i="peek"></button>
  </div>
  <div class="msg" id="m1"></div>
</div>

<h2 data-i="env"></h2>
<div class="card">
  <label for="e" data-i="envhint" data-html></label>
  <textarea id="e" placeholder="GROQ_API_KEY=gsk_...&#10;STRIPE_KEY=sk_live_..." spellcheck="false"></textarea>
  <div class="row"><button id="imp" data-i="importall"></button></div>
  <div class="msg" id="m2"></div>
</div>

<h2 data-i="stored_h"></h2>
<div class="card"><ul id="list"></ul>
  <p class="hint" data-i="use" data-html></p>
</div>

<div class="row" style="margin-top:2rem">
  <button class="ghost" id="quit" data-i="quit"></button>
</div>

<script>
const S = {
  pt: {title:'Cofre', sub:'O valor vai para o chaveiro do sistema (e para o Proton Pass, se você estiver logado). Não passa pela conversa, não vai para o transcript, e a lista abaixo nunca mostra o conteúdo.',
    one:'Guardar uma chave', name:'Nome', value:'Valor', paste:'cole aqui', save:'Guardar', peek:'Mostrar valor',
    env:'Ou colar um .env inteiro', envhint:'Uma chave por linha. Aceita <code>A=b</code>, <code>export A=b</code> e <code>A: b</code>.',
    importall:'Importar todas', stored_h:'Guardadas', use:'Use com <code>nonstop run NOME -- seu-comando</code>.',
    quit:'Fechar o cofre', none:'nada guardado ainda', remove:'remover', fill:'preencha nome e valor', pastefirst:'cole algo primeiro',
    need:(n, r) => 'Preciso de <b>' + n + '</b>' + (r ? ' — ' + r : '') + '.<br>Digite o valor abaixo e clique Guardar. Eu nunca vejo o que você digita aqui.',
    closed:'Cofre fechado', closetab:'Pode fechar esta aba.',
    bad_name:'o nome precisa parecer variável de ambiente (LETRAS_E_UNDERLINE)', empty:'valor vazio',
    store_failed:m => 'não consegui gravar ' + m.name + ' no chaveiro', keychain:'chaveiro',
    stored:m => 'guardado ' + m.name + ' (' + m.n + ' caracteres) em: ' + m.where.map(w => w === 'keychain' ? 'chaveiro' : w).join(', '),
    no_lines:'nenhuma linha NOME=valor reconhecida',
    imported:m => (m.done.length ? m.done.length + ' guardada(s): ' + m.done.join(', ') : '') + (m.failed.length ? ' — falharam: ' + m.failed.join(', ') : '')},
  en: {title:'Vault', sub:'The value goes to the system keychain (and to Proton Pass when you are signed in). It never passes through the conversation or the transcript, and the list below never shows it.',
    one:'Store a key', name:'Name', value:'Value', paste:'paste here', save:'Save', peek:'Show value',
    env:'Or paste a whole .env', envhint:'One key per line. Accepts <code>A=b</code>, <code>export A=b</code> and <code>A: b</code>.',
    importall:'Import all', stored_h:'Stored', use:'Use with <code>nonstop run NAME -- your-command</code>.',
    quit:'Close the vault', none:'nothing stored yet', remove:'remove', fill:'fill in name and value', pastefirst:'paste something first',
    need:(n, r) => 'I need <b>' + n + '</b>' + (r ? ' — ' + r : '') + '.<br>Type the value below and click Save. I never see what you type here.',
    closed:'Vault closed', closetab:'You can close this tab.',
    bad_name:'the name must look like an environment variable (LETTERS_AND_UNDERSCORES)', empty:'empty value',
    store_failed:m => 'could not store ' + m.name + ' in the keychain', keychain:'keychain',
    stored:m => 'stored ' + m.name + ' (' + m.n + ' characters) in: ' + m.where.join(', '),
    no_lines:'no NAME=value line recognised',
    imported:m => (m.done.length ? m.done.length + ' stored: ' + m.done.join(', ') : '') + (m.failed.length ? ' — failed: ' + m.failed.join(', ') : '')}
};
const Q = new URLSearchParams(location.search);
const T = Q.get('t') || '';
const NEED = (Q.get('need') || '').replace(/[<>&"]/g, '');
const REASON = (Q.get('reason') || '').replace(/[<>&"]/g, '');
let L;
try { L = localStorage.getItem('nonstop.lang'); } catch (e) {}
if (!S[L]) L = S[Q.get('lang')] ? Q.get('lang') : (navigator.language || '').toLowerCase().startsWith('pt') ? 'pt' : 'en';

function paint() {
  const d = S[L];
  document.documentElement.lang = L === 'pt' ? 'pt-BR' : 'en';
  document.title = d.title + ' — nonstop';
  for (const el of document.querySelectorAll('[data-i]')) {
    const v = d[el.dataset.i];
    if (el.hasAttribute('data-html')) el.innerHTML = v; else el.textContent = v;
  }
  document.getElementById('v').placeholder = d.paste;
  document.getElementById('lang').textContent = L === 'pt' ? 'EN' : 'PT';
  const a = document.getElementById('ask');
  if (NEED) { a.hidden = false; a.innerHTML = d.need(NEED, REASON); }
  refresh();
}
document.getElementById('lang').onclick = () => {
  L = L === 'pt' ? 'en' : 'pt';
  try { localStorage.setItem('nonstop.lang', L); } catch (e) {}
  for (const id of ['m1', 'm2']) document.getElementById(id).textContent = '';
  paint();
};

const api = (path, body) => fetch(path + '?t=' + encodeURIComponent(T), {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(body || {})
}).then(r => r.json());
const text = m => { const f = S[L][m && m.key]; return typeof f === 'function' ? f(m) : (f || ''); };
const say = (el, ok, t) => { el.className = 'msg ' + (ok ? 'ok' : 'bad'); el.textContent = t; };

async function refresh() {
  const r = await api('/list');
  const ul = document.getElementById('list');
  ul.innerHTML = '';
  if (!r.names.length) {
    const li = document.createElement('li');
    li.style.cssText = 'color:var(--muted);font-family:inherit';
    li.textContent = S[L].none;
    ul.appendChild(li);
    return;
  }
  for (const n of r.names) {
    const li = document.createElement('li');
    li.textContent = n;
    const b = document.createElement('button');
    b.textContent = S[L].remove;
    b.onclick = async () => { await api('/delete', {name: n}); refresh(); };
    li.appendChild(b);
    ul.appendChild(li);
  }
}

document.getElementById('peek').onclick = () => {
  const v = document.getElementById('v');
  v.type = v.type === 'password' ? 'text' : 'password';
};

document.getElementById('save').onclick = async () => {
  const n = document.getElementById('n').value.trim();
  const v = document.getElementById('v').value;
  const m = document.getElementById('m1');
  if (!n || !v) { say(m, false, S[L].fill); return; }
  const r = await api('/add', {name: n, value: v});
  say(m, r.ok, text(r.msg));
  if (r.ok) { document.getElementById('n').value = ''; document.getElementById('v').value = ''; refresh(); }
};

document.getElementById('imp').onclick = async () => {
  const t = document.getElementById('e').value;
  const m = document.getElementById('m2');
  if (!t.trim()) { say(m, false, S[L].pastefirst); return; }
  const r = await api('/import', {text: t});
  say(m, r.ok, text(r.msg));
  if (r.ok) { document.getElementById('e').value = ''; refresh(); }
};

document.getElementById('quit').onclick = async () => {
  await api('/quit');
  document.body.innerHTML = '<div class="wrap"><h1></h1><p class="sub"></p></div>';
  document.querySelector('h1').textContent = S[L].closed;
  document.querySelector('.sub').textContent = S[L].closetab;
};

if (NEED) addEventListener('DOMContentLoaded', () => {
  document.getElementById('n').value = NEED;
  document.getElementById('v').focus();
});
paint();
setInterval(() => api('/ping'), 60000);
</script>
</div></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "nonstop-vault"

    def log_message(self, *a):  # never log request bodies or paths with tokens
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _token_ok(self) -> bool:
        _, _, query = self.path.partition("?")
        for part in query.split("&"):
            if part.startswith("t="):
                from urllib.parse import unquote

                return secrets.compare_digest(unquote(part[2:]), TOKEN)
        return False

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        if not self._host_ok():
            self._send(400, b"bad host", "text/plain")
            return
        if self.path.split("?")[0] != "/" or not self._token_ok():
            self._send(404, b"not found", "text/plain")
            return
        touch()
        self._send(200, PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok() or not self._token_ok():
            self._json({"ok": False, "message": "unauthorised"}, 403)
            return
        touch()
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._json({"ok": False, "message": "too large"}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "message": "bad request"}, 400)
            return

        if path == "/ping":
            self._json({"ok": True})
        elif path == "/list":
            code, out = cli("secret", "list")
            names = [ln.strip() for ln in out.splitlines() if ln.startswith("  ") and ln.strip()]
            self._json({"ok": code == 0, "names": names})
        elif path == "/add":
            ok, msg = store(str(payload.get("name", "")), str(payload.get("value", "")))
            self._json({"ok": ok, "msg": msg})
        elif path == "/import":
            pairs = parse_env(str(payload.get("text", "")))
            if not pairs:
                self._json({"ok": False, "msg": {"key": "no_lines"}})
                return
            done, failed = [], []
            for name, value in pairs:
                ok, _ = store(name, value)
                (done if ok else failed).append(name)
            self._json({"ok": bool(done), "msg": {"key": "imported", "done": done, "failed": failed}})
        elif path == "/delete":
            name = str(payload.get("name", ""))
            if not NAME_RE.match(name):
                self._json({"ok": False, "message": "invalid name"}, 400)
                return
            code, _ = cli("secret", "rm", name)
            self._json({"ok": code == 0})
        elif path == "/quit":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"ok": False, "message": "not found"}, 404)


def main() -> int:
    if not os.path.exists(CLI):
        print(f"cannot find the CLI at {CLI}", file=sys.stderr)
        return 1

    port = 0
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    real_port = httpd.socket.getsockname()[1]
    from urllib.parse import urlencode

    params = {"t": TOKEN}
    for i, a in enumerate(sys.argv):
        if a == "--need" and i + 1 < len(sys.argv):
            params["need"] = sys.argv[i + 1]
        if a == "--reason" and i + 1 < len(sys.argv):
            params["reason"] = sys.argv[i + 1]
        if a == "--lang" and i + 1 < len(sys.argv):
            params["lang"] = sys.argv[i + 1]
    url = f"http://127.0.0.1:{real_port}/?{urlencode(params)}"

    def reaper():
        while True:
            time.sleep(20)
            with _lock:
                idle = time.time() - _last_seen
            if idle > IDLE_TIMEOUT:
                httpd.shutdown()
                return

    threading.Thread(target=reaper, daemon=True).start()

    # flush=True: stdout is block-buffered when redirected, and a vault whose
    # URL never appears is a vault nobody can open.
    pt = params.get("lang", os.environ.get("NONSTOP_LANG", "")).startswith("pt")
    print("Cofre aberto em:" if pt else "Vault open at:", flush=True)
    print(f"  {url}", flush=True)
    print(flush=True)
    print(
        "Loopback apenas, token de uso único, fecha sozinho após 15 min parado."
        if pt
        else "Loopback only, one-time token, closes itself after 15 idle minutes.",
        flush=True,
    )
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Cofre fechado." if pt else "Vault closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

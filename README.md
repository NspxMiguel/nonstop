<h1 align="center">nonstop</h1>

<p align="center">
  <b>Keep a <a href="https://claude.com/claude-code">Claude Code</a> session working until you say it is done.</b><br>
  A task list it has to empty, a sweep for loose ends before it may stop, and a keychain vault for API keys.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <img alt="Platform: macOS | Linux" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white">
  <a href="https://github.com/NspxMiguel/nonstop/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/NspxMiguel/nonstop/test.yml?branch=main&label=tests"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#use">Use</a> ·
  <a href="#keys-without-pasting-them-into-the-chat">Keys</a> ·
  <a href="#language">Language</a> ·
  <a href="#development">Development</a>
</p>

Keep a [Claude Code](https://claude.com/claude-code) session working until **you**
say it is done — with a task list it has to empty, a sweep for loose ends before
it may stop, and a keychain vault so it can use your API keys without ever
seeing them in the chat.

Ask an agent to "fix the button" and it fixes the button and hands the turn
back. `nonstop` turns that ask into the start of a work session: fix it, prove
it, check what is next to it, and keep going through the list until you type
**`chega`** (Portuguese for "enough").

It is **off by default**. Nothing changes until you type `/autonomous`.

## Install

```bash
git clone https://github.com/NspxMiguel/nonstop.git
cd nonstop && ./install.sh
```

Requires macOS or Linux and `python3` (standard library only). The vault
commands use the macOS keychain.

The installer puts the CLI at `~/.local/bin/nonstop`, registers one `Stop` hook
in `~/.claude/settings.json` (keeping every other hook you have, with a backup
at `settings.json.bak-nonstop`) and adds three skills. Running Claude Code
sessions pick the hook up without a restart. `./uninstall.sh` removes all of it
and keeps your secrets.

## Use

| You type | What happens |
| --- | --- |
| `/autonomous` | Nonstop mode on for this session |
| `/tarefas` | Shows the task list (`tarefas` = tasks) |
| `/senhas` | How to store and use keys (`senhas` = passwords) |
| `chega` · `para tudo` · `pode parar` | Stops right away (only as a short message) |
| `/autonomous off` | Same, explicitly |

### How it keeps going

1. On `/autonomous`, Claude writes your request into a task list —
   the literal ask plus its test, the neighbours it can break, and the boring
   items (README, commit, push).
2. It works the list. A task only closes **with proof**:

   ```bash
   nonstop done 3 --proof "bun test: 42 passed"
   ```

3. The `Stop` hook refuses to end the turn while any task is open, and shows
   the open items back to it.
4. Once the list is empty it gets **one sweep**: re-read the request, look for
   the same bug elsewhere, unrun tests, unpushed commits, servers left running.
   Only then may it finish, with `[NONSTOP:DONE]` and an evidence list.
5. Anything only you can do — a password, a 6-digit code, spending money, a
   real decision — is parked with `nonstop blocked <n> --reason "..."` and the
   work moves on to the next item instead of stopping.

Safety valves so a stuck loop cannot burn your plan: 12 hours per activation,
300 blocks per prompt, or 3 blocks in a row with no tool call in between.
Plain conversation (a turn with no tool calls) always passes.

<details>
<summary><b>Task list</b></summary>


```bash
nonstop add "fix save button" "test empty form" "commit and push"
nonstop list                      # also: nonstop tarefas
nonstop doing 1
nonstop done 1 --proof "..."
nonstop blocked 2 --reason "needs the App Store password"
nonstop status
```

State lives in `~/.claude/nonstop/sessions/<session>.json`, one file per
session, pruned after 7 idle days. Every hook decision is logged to
`~/.claude/nonstop/log.jsonl`.

</details>

### Keys without pasting them into the chat

```bash
nonstop secret list                              # names only, never values
nonstop secret set GROQ_API_KEY --clipboard      # clipboard → keychain
nonstop run GROQ_API_KEY -- sh -c 'curl -H "Authorization: Bearer $GROQ_API_KEY" ...'
nonstop harvest                                  # find keys already in .env files and CLI configs
nonstop harvest --apply                          # import them
nonstop request STRIPE_KEY "to test checkout"    # opens a local page where YOU paste it
nonstop vault                                    # same page, for a whole .env
```

- Values go in through stdin or the clipboard and out through the child's
  environment. They never appear in argv, stdout or the transcript.
- The vault page listens on `127.0.0.1` only, needs a one-time token, checks
  the `Host` header and closes itself after 15 idle minutes.
- Keys are stored as `claude-autonomous:<NAME>` in the keychain (set
  `NONSTOP_SECRET_PREFIX` to change it), so keys saved by the older
  `claude-autonomous` tool keep working.

This stores keys you already have. It does not hold account passwords, fill
login forms or sign in as you.

## Language

The CLI and the vault page speak Portuguese and English. The system locale
decides; `nonstop lang pt|en` saves a choice, and `NONSTOP_LANG=pt|en`
overrides both for one run. The vault page also has its own PT/EN toggle.

## Development

```bash
python3 -m unittest discover -v tests
uvx ruff check . && uvx ruff format --check .
```

The tests drive the real hook with synthetic transcripts: off by default,
`/autonomous` on and off, open tasks blocking even with the done marker,
proof required, the sweep, `chega` versus a long prompt that only mentions it,
and the idle valve.

## License

[MIT](LICENSE) © nspxmiguel

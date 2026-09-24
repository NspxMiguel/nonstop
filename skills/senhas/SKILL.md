---
name: senhas
description: Store and use API keys and tokens from the macOS keychain without the value ever entering the conversation. Invoked as /senhas; also use when a command needs a key ("precisa da chave do Groq", "API key", "token", "guarda essa chave", "coloca no chaveiro"), or before asking the user to paste a secret.
---

# /senhas — keys in the keychain, never in the chat

Keys live in the macOS keychain as `claude-autonomous:<NAME>`. Rules that never bend:

- **Never print a value.** No `echo`, no `get`, no screenshot or `get_page_text`
  of a page showing a key — the transcript is uploaded.
- **Use keys through `run`**, which exports them into the child process only:

  ```bash
  nonstop secret list                                  # names only
  nonstop run GROQ_API_KEY -- sh -c 'curl -s -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models'
  nonstop run GROQ_API_KEY -- python3 script.py        # script reads os.environ
  ```

  The single quotes matter: without them your shell expands `$GROQ_API_KEY`
  (empty) before `run` ever sets it.

## Getting a key in, without anyone typing it into the chat

1. **Already on the machine?** `nonstop harvest` lists keys found in `.env`
   files, shell profiles and CLI configs (names and lengths only);
   `nonstop harvest --apply` imports them.
2. **On a dashboard the user is signed into?** Click the page's own copy button,
   then `nonstop secret set NAME --clipboard` — clipboard straight to keychain.
3. **Only the user has it?** `nonstop request NAME "why it is needed"` opens a
   local page (127.0.0.1, one-time token) where **they** paste it. Tell them it
   opened and wait for `nonstop secret has NAME` to succeed.
4. Many at once: `nonstop vault` opens the same page for pasting a whole `.env`.

Passwords of accounts, 6-digit codes and logins stay the user's: this stores
keys they already have, it does not sign in as them.

---
name: tarefas
description: Show and manage this session's nonstop task list. Invoked as /tarefas; also use when the user asks "o que falta?", "quais as tarefas", "mostra a lista", "what's left", "show the tasks".
---

# /tarefas — the session's task list

Run `nonstop list` and show the user the result as it is (numbers, state, proof).

States: `[ ]` open, `[~]` in progress, `[x]` done with proof, `[!]` blocked on the user.

If the user passed arguments, act on them:

| They wrote | Run |
| --- | --- |
| `add <texto>` / `adiciona <texto>` | `nonstop add "<texto>"` |
| `done <n>` | only with real proof: `nonstop done <n> --proof "..."` |
| `reopen <n>` | `nonstop reopen <n>` |
| `clear` / `limpa` | `nonstop clear` |

Then keep working on the first open item if /autonomous is on (`nonstop status`).

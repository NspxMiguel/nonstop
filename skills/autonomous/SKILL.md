---
name: autonomous
description: Turn on nonstop mode — keep working until the user says "chega". Invoked by the user as /autonomous (or "/autonomous off" to stop). Also use when the user says "modo autônomo", "trabalha até eu falar chega", "não para", "keep working until I say stop", "work like codex for hours".
---

# /autonomous — work until "chega"

The user typed `/autonomous`. From now on this session does not hand the turn
back until the work is really finished or the user says **"chega"**.

If the arguments say `off` (or `stop`, `desliga`): run `nonstop off`, confirm in
one line, and stop here.

## Start

1. `nonstop on --task "<the request, in the user's words>"` (the Stop hook also
   notices /autonomous by itself; this records the request for the sweep).
2. Turn the request into a task list **before touching code** — the literal
   ask, its test, the neighbours it can break, the obvious follow-ups, and the
   boring ones (README, i18n, commit, push, check it running):

   ```bash
   nonstop add "fix the save button" "test save on empty form" "check the edit screen uses the same handler" "commit and push"
   ```

## The loop

- `nonstop doing <n>` → do it → `nonstop done <n> --proof "command and what it printed"`.
  No proof, no close; "should work" is not proof.
- **The literal ask is the floor, not the ceiling.** After "fix the button":
  reproduce, fix, test for real, look at the screens next to it, hunt the same
  bug elsewhere, fix what you see on the way, raise the quality of what you touched.
- Found more work? `nonstop add "..."` — it is not a reason to stop.
- Only the user can do it (password, 6-digit code, money, a real decision)?
  `nonstop blocked <n> --reason "..."` and go to the next item.
- `nonstop list` shows where you are.

## Ending

The Stop hook refuses to end the turn while any task is open, then asks for one
sweep when the list is empty. After a real sweep, finish with
`[NONSTOP:DONE]` plus a short evidence list, or `[NONSTOP:BLOCKED]` plus what
only the user can do. Never "quer que eu continue?", never "agora só falta X"
when X is yours.

"chega", "para tudo", "pode parar" (a short message) or `/autonomous off` end
it. Safety valves: 12 hours per activation, 300 blocks per prompt, or 3 blocks
in a row with no tool call in between.

Long sessions must stay cheap: fan-out reading on Haiku subagents, trim command
output, and warn before auto-compact.

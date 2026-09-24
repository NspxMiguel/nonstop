"""Tests for the Stop hook and the task CLI. Run: python3 -m unittest discover tests"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "nonstop.py"


class Harness:
    """A fake session: builds a transcript and calls the real script."""

    def __init__(self, home: Path):
        self.home = home
        self.transcript = home / "t.jsonl"
        self.entries: list[dict] = []
        self.n = 0

    def env(self) -> dict:
        return dict(os.environ, NONSTOP_HOME=str(self.home), NONSTOP_LANG="en", CLAUDE_CODE_SESSION_ID="s1")

    def prompt(self, text: str) -> None:
        self.n += 1
        self.entries.append({"type": "user", "uuid": f"u{self.n}", "message": {"content": text}})
        self._flush()

    def command(self, args: str = "") -> None:
        body = "<command-message>autonomous</command-message>\n<command-name>/autonomous</command-name>"
        if args:
            body += f"\n<command-args>{args}</command-args>"
        self.prompt(body)

    def tools(self, count: int = 1) -> None:
        for _ in range(count):
            self.n += 1
            self.entries.append(
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": str(self.n)}]}}
            )
            self.entries.append(
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": str(self.n)}]}}
            )
        self._flush()

    def say(self, text: str) -> None:
        self.entries.append({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
        self._flush()

    def _flush(self) -> None:
        self.transcript.write_text("\n".join(json.dumps(e) for e in self.entries))

    def stop(self) -> dict | None:
        data = json.dumps({"session_id": "s1", "transcript_path": str(self.transcript)})
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "hook"],
            input=data,
            capture_output=True,
            check=False,
            text=True,
            env=self.env(),
        )
        self.assert_ok(out)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def cli(self, *args: str, ok: bool = True) -> str:
        out = subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, check=False, text=True, env=self.env()
        )
        if ok:
            self.assert_ok(out)
        return out.stdout + out.stderr

    @staticmethod
    def assert_ok(out: subprocess.CompletedProcess) -> None:
        if out.returncode != 0:
            raise AssertionError(out.stderr)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = Harness(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_by_default(self):
        self.h.prompt("fix the button")
        self.h.tools(5)
        self.h.say("done")
        self.assertIsNone(self.h.stop())

    def test_autonomous_turns_it_on_and_sweeps_once(self):
        self.h.command()
        self.h.tools(3)
        self.h.say("done")
        first = self.h.stop()
        self.assertIn("sweep", first["reason"])
        self.h.tools(1)
        self.h.say("all good\n[NONSTOP:DONE]\n- tests pass")
        self.assertIsNone(self.h.stop())

    def test_marker_without_sweep_is_refused(self):
        self.h.command()
        self.h.tools(2)
        self.h.say("[NONSTOP:DONE]")
        self.assertIsNotNone(self.h.stop())

    def test_open_tasks_block_even_with_marker(self):
        self.h.command()
        self.h.cli("add", "fix button", "write test")
        self.h.tools(2)
        self.h.say("[NONSTOP:DONE]")
        r = self.h.stop()
        self.assertIn("fix button", r["reason"])
        self.h.tools(1)
        r = self.h.stop()
        self.assertIn("write test", r["reason"])

    def test_tasks_closed_then_sweep_then_allow(self):
        self.h.command()
        self.h.cli("add", "only item")
        self.h.tools(1)
        self.assertIsNotNone(self.h.stop())
        self.h.cli("done", "1", "--proof", "pytest: 3 passed")
        self.h.tools(1)
        self.assertIn("sweep", self.h.stop()["reason"])
        self.h.tools(1)
        self.h.say("[NONSTOP:DONE] evidence")
        self.assertIsNone(self.h.stop())

    def test_blocked_tasks_do_not_hold_the_session(self):
        self.h.command()
        self.h.cli("add", "needs password")
        self.h.cli("blocked", "1", "--reason", "only the user has it")
        self.h.tools(1)
        self.assertIn("sweep", self.h.stop()["reason"])

    def test_done_requires_proof(self):
        self.h.cli("add", "x")
        out = self.h.cli("done", "1", ok=False)
        self.assertIn("proof", out)

    def test_chega_turns_it_off(self):
        self.h.command()
        self.h.tools(1)
        self.assertIsNotNone(self.h.stop())
        self.h.prompt("chega")
        self.h.tools(1)
        self.assertIsNone(self.h.stop())
        self.h.prompt("now fix another thing")
        self.h.tools(3)
        self.assertIsNone(self.h.stop())  # stays off until /autonomous again

    def test_long_prompt_mentioning_chega_does_not_turn_it_off(self):
        self.h.command()
        self.h.prompt("keep working until I say chega, fix everything in the app")
        self.h.tools(2)
        self.assertIsNotNone(self.h.stop())

    def test_autonomous_off_argument(self):
        self.h.command()
        self.h.command("off")
        self.h.tools(2)
        self.assertIsNone(self.h.stop())

    def test_idle_valve_releases_a_stuck_loop(self):
        self.h.command()
        self.h.cli("add", "never done")
        self.h.tools(1)
        results = [self.h.stop() for _ in range(4)]
        self.assertIsNotNone(results[0])
        self.assertIsNone(results[-1])

    def test_plain_conversation_passes(self):
        self.h.command()
        self.h.prompt("what does this function do?")
        self.h.say("it adds two numbers")
        self.assertIsNone(self.h.stop())

    def test_cli_on_off_status(self):
        self.assertIn("Mode: off", self.h.cli("status"))
        self.h.cli("on", "--task", "ship it")
        status = self.h.cli("status")
        self.assertIn("Mode: on", status)
        self.assertIn("ship it", status)
        self.h.cli("off")
        self.assertIn("Mode: off", self.h.cli("status"))


if __name__ == "__main__":
    unittest.main()

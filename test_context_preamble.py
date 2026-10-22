"""Context-management regression tests (feat/context).

Covers the two token-waste bugs found on this branch:

1. The Botty system directive was re-inlined into EVERY user message, even though
   `hermes chat --continue` already keeps it in the session history. On a 31-turn
   session that was ~30 duplicate copies (~48% of each prompt).
2. The auto-compaction trigger called `distill_and_compact_session()` from inside
   `ask()`, where `_ask_live()` is true by definition, so it returned
   "Botty is processing a request" on every turn and the session grew forever.

Isolates all module paths into a temp dir (same pattern as test_security.py).
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import botty_backend


class TestContextPreamble(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.test_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_mem_dir = Path(self.test_dir.name) / "memories"
        self.hermes_mem_dir.mkdir(parents=True, exist_ok=True)
        self.state_db = Path(self.test_dir.name) / "state.db"

        self.orig = {}
        for name in ("BOTTY_DATA_DIR", "HISTORY_FILE", "HISTORY_ARCHIVE_FILE",
                     "CONFIG_FILE", "STATUS_FILE", "LOCK_FILE", "HISTORY_LOCK_FILE",
                     "HERMES_MEMORY_DIR", "PROPOSALS_FILE", "BOTTY_LOG_FILE",
                     "CONTEXT_BRIDGE_FILE", "HERMES_STATE_DB", "HERMES_CONFIG_FILE",
                     "VAULT_FILE", "APPROVED_STAGING_DIR"):
            self.orig[name] = getattr(botty_backend, name)
        botty_backend.BOTTY_DATA_DIR = self.data_dir
        botty_backend.HISTORY_FILE = self.data_dir / "history.json"
        botty_backend.HISTORY_ARCHIVE_FILE = self.data_dir / "history_archive.jsonl"
        botty_backend.CONFIG_FILE = self.data_dir / "config.json"
        botty_backend.STATUS_FILE = self.data_dir / "status.json"
        botty_backend.LOCK_FILE = self.data_dir / "running.pid"
        botty_backend.HISTORY_LOCK_FILE = self.data_dir / ".history.lock"
        botty_backend.HERMES_MEMORY_DIR = self.hermes_mem_dir
        botty_backend.PROPOSALS_FILE = self.data_dir / "pending_proposals.json"
        botty_backend.BOTTY_LOG_FILE = self.data_dir / "botty.log"
        botty_backend.CONTEXT_BRIDGE_FILE = self.data_dir / "context_bridge.txt"
        botty_backend.HERMES_STATE_DB = self.state_db
        botty_backend.HERMES_CONFIG_FILE = self.data_dir / "hermes_config.yaml"
        botty_backend.VAULT_FILE = self.data_dir / "vault.enc"
        botty_backend.APPROVED_STAGING_DIR = self.data_dir / "approved_proposals"
        botty_backend.APPROVED_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for name, val in self.orig.items():
            setattr(botty_backend, name, val)
        self.test_dir.cleanup()

    # --- helpers ---

    def _make_state_db(self, user_messages, title="botty-widget"):
        con = sqlite3.connect(str(self.state_db))
        try:
            con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL)")
            con.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT, role TEXT, content TEXT)"
            )
            con.execute("INSERT INTO sessions VALUES ('s1', ?, 1000.0)", (title,))
            for content in user_messages:
                con.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES ('s1', 'user', ?)",
                    (content,),
                )
            con.commit()
        finally:
            con.close()

    def _run_ask_capture(self, sandboxed=True):
        """Run ask() with a fake engine and return the prompt written for Hermes."""
        captured = {}

        def fake_run(cmd, **kwargs):
            for i, part in enumerate(cmd):
                if part == "--query-file":
                    captured["prompt"] = Path(cmd[i + 1]).read_text(encoding="utf-8")
            return {"ok": True, "returncode": 0, "stdout": "ok", "stderr": "",
                    "timed_out": False, "output_truncated": False, "error": None}

        with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_run), \
             mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={"model": "test-model", "provider": "test"}), \
             mock.patch.object(botty_backend, "get_sandbox_mode", return_value=sandboxed), \
             mock.patch.object(botty_backend, "send_system_notification", return_value=None), \
             mock.patch.object(botty_backend, "update_encrypted_vault", return_value=True):
            res = botty_backend.ask("ciao botty")
        self.assertTrue(res.get("ok"), res)
        return captured["prompt"]

    # --- 1. preamble detection ---

    def test_no_state_db_needs_preamble(self):
        self.assertFalse(botty_backend.hermes_session_has_preamble())

    def test_state_db_without_session_needs_preamble(self):
        con = sqlite3.connect(str(self.state_db))
        con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL)")
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT)")
        con.commit()
        con.close()
        self.assertFalse(botty_backend.hermes_session_has_preamble())

    def test_session_carrying_directive_skips_preamble(self):
        self._make_state_db(["prima domanda", "[SYSTEM DIRECTIVE]\nYou are acting as Botty.…"])
        self.assertTrue(botty_backend.hermes_session_has_preamble())

    def test_session_without_marker_needs_preamble(self):
        self._make_state_db(["prima domanda", "seconda domanda"])
        self.assertFalse(botty_backend.hermes_session_has_preamble())

    def test_archived_session_is_treated_as_fresh(self):
        # Compaction renames the session to archived-*: the next turn must send
        # the directive again, otherwise the new session loses it entirely.
        self._make_state_db(["[SYSTEM DIRECTIVE]\nold"], title="archived-botty-widget-123")
        self.assertFalse(botty_backend.hermes_session_has_preamble())

    def test_unreadable_db_is_treated_as_fresh(self):
        # A corrupt/unreadable DB must fall back to sending the directive.
        self.state_db.write_text("not a database", encoding="utf-8")
        self.assertFalse(botty_backend.hermes_session_has_preamble())

    # --- 2. prompt composition ---

    def test_prompt_omits_directive_on_continued_session(self):
        self._make_state_db(["[SYSTEM DIRECTIVE]\nYou are acting as Botty.…"])
        prompt = self._run_ask_capture()
        self.assertNotIn("[SYSTEM DIRECTIVE]", prompt)
        self.assertNotIn("You are acting as Botty", prompt)
        self.assertIn("ciao botty", prompt)
        self.assertIn("[MODE: SANDBOXED", prompt)

    def test_prompt_includes_directive_on_fresh_session(self):
        prompt = self._run_ask_capture()
        self.assertIn("[SYSTEM DIRECTIVE]", prompt)
        self.assertEqual(prompt.count("You are acting as Botty"), 1)
        self.assertIn("[MODE: SANDBOXED", prompt)

    def test_unrestricted_mode_line_on_bypass_run(self):
        prompt = self._run_ask_capture(sandboxed=False)
        self.assertIn("[MODE: UNRESTRICTED RUN", prompt)
        self.assertNotIn("[MODE: SANDBOXED", prompt)

    def test_bridge_sent_only_with_the_directive(self):
        # The continuity bridge is a session preamble too: after the first turn it
        # already lives in the session history and must not be repeated.
        botty_backend.CONTEXT_BRIDGE_FILE.write_text("riassunto del contesto precedente", encoding="utf-8")
        fresh = self._run_ask_capture()
        self.assertIn("riassunto del contesto precedente", fresh)

        botty_backend.CONTEXT_BRIDGE_FILE.write_text("riassunto del contesto precedente", encoding="utf-8")
        self._make_state_db(["[SYSTEM DIRECTIVE]\nYou are acting as Botty.…"])
        later = self._run_ask_capture()
        self.assertNotIn("riassunto del contesto precedente", later)

    def test_mode_line_is_always_last(self):
        prompt = self._run_ask_capture()
        self.assertTrue(prompt.rstrip().endswith("wait for the user's approval.]"))

    def test_build_hermes_prompt_shape(self):
        out = botty_backend.build_hermes_prompt("DOMANDA", "DIRECTIVE", "MODE", include_directive=True)
        self.assertIn("[SYSTEM DIRECTIVE]\nDIRECTIVE\n[END SYSTEM DIRECTIVE]", out)
        self.assertTrue(out.endswith("MODE"))
        short = botty_backend.build_hermes_prompt("DOMANDA", "DIRECTIVE", "MODE", include_directive=False)
        self.assertNotIn("DIRECTIVE", short)
        self.assertIn("DOMANDA", short)

    # --- 3. auto-compaction actually runs ---

    def test_auto_compaction_is_spawned_after_lock_release(self):
        msgs = [{"id": "m%d" % i, "role": "user", "content": "t%d" % i,
                 "timestamp": 1700000000 + i, "attachments": [], "actions": []}
                for i in range(botty_backend.get_auto_compact_threshold() + 2)]
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": msgs, "rev": 1}))

        seen = {}

        def fake_spawn():
            seen["lock_present"] = botty_backend.LOCK_FILE.exists()
            seen["called"] = True

        with mock.patch.object(botty_backend, "_spawn_detached_compaction", side_effect=fake_spawn):
            self._run_ask_capture()

        self.assertTrue(seen.get("called"), "auto-compaction was never triggered")
        self.assertFalse(seen["lock_present"],
                         "compaction must start only after the ask lock is released")

    def test_no_auto_compaction_below_threshold(self):
        seen = {"called": False}

        def fake_spawn():
            seen["called"] = True

        with mock.patch.object(botty_backend, "_spawn_detached_compaction", side_effect=fake_spawn):
            self._run_ask_capture()
        self.assertFalse(seen["called"])

    def test_distill_refuses_under_live_ask(self):
        # The reason the in-ask trigger never worked: the compactor sees our own
        # PID file and bails out. Keep that guard, it protects manual compaction.
        botty_backend.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        botty_backend.LOCK_FILE.write_text(str(__import__("os").getpid()), encoding="utf-8")
        res = botty_backend.distill_and_compact_session(force=True)
        self.assertFalse(res["ok"])
        self.assertIn("processing", res["error"])

    # --- 4. clearing the chat also drops the stale bridge ---

    def test_clear_history_removes_continuity_bridge(self):
        botty_backend.CONTEXT_BRIDGE_FILE.write_text("vecchio contesto", encoding="utf-8")
        with mock.patch.object(botty_backend, "reset_hermes_session", return_value={"ok": True}):
            res = botty_backend.clear_history()
        self.assertTrue(res["ok"])
        self.assertFalse(botty_backend.CONTEXT_BRIDGE_FILE.exists())


if __name__ == "__main__":
    unittest.main()

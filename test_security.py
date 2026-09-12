"""Security regression tests for botty hardening (H1/H2/M1/M2/L2 + round 3).

Isolates all module paths into a temp dir (same pattern as
test_compaction.py) so no real user data is touched.
"""
import errno
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import botty_backend


class TestSecurityHardening(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.test_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_mem_dir = Path(self.test_dir.name) / "memories"
        self.hermes_mem_dir.mkdir(parents=True, exist_ok=True)

        self.orig = {}
        for name in ("BOTTY_DATA_DIR", "HISTORY_FILE", "HISTORY_ARCHIVE_FILE",
                     "CONFIG_FILE", "STATUS_FILE", "LOCK_FILE", "HISTORY_LOCK_FILE",
                     "HERMES_MEMORY_DIR", "PROPOSALS_FILE", "BOTTY_LOG_FILE"):
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

    def tearDown(self):
        for name, val in self.orig.items():
            setattr(botty_backend, name, val)
        self.test_dir.cleanup()

    def _history(self):
        return json.loads(botty_backend.HISTORY_FILE.read_text())

    # --- H1: model-derived facts must stage, never write live ---

    def test_h1_from_model_stages_proposal_not_live(self):
        res = botty_backend.add_memory("Model says the sky is blue", from_model=True)
        self.assertTrue(res["ok"])
        self.assertIn("review", res["message"])
        self.assertFalse((self.hermes_mem_dir / "MEMORY.md").exists())
        props = botty_backend.get_proposals()
        self.assertEqual(props["count"], 1)
        self.assertFalse(props["proposals"][0]["is_user_fact"])

    def test_h1_from_model_duplicate_rejected(self):
        botty_backend.add_memory("Same fact here", from_model=True)
        res = botty_backend.add_memory("Same fact here", from_model=True)
        self.assertFalse(res["ok"])

    def test_h1_from_model_overlong_rejected(self):
        res = botty_backend.add_memory("x" * 700, from_model=True)
        self.assertFalse(res["ok"])

    def test_h1_user_typed_still_writes_live(self):
        res = botty_backend.add_memory("User typed fact", from_model=False)
        self.assertTrue(res["ok"])
        live = (self.hermes_mem_dir / "MEMORY.md").read_text()
        self.assertIn("User typed fact", live)
        self.assertEqual(botty_backend.get_proposals()["count"], 0)

    # --- H2: sandboxed non-Hermes ask refused, lock cleaned ---

    def test_h2_sandboxed_non_hermes_refused(self):
        botty_backend.set_active_engine("omp")
        res = botty_backend.ask("do a thing")
        self.assertFalse(res["ok"])
        self.assertIn("Hermes", res["error"])
        self.assertFalse(botty_backend.LOCK_FILE.exists())
        roles = [m["role"] for m in self._history()["messages"]]
        self.assertEqual(roles, ["user", "assistant"])

    # --- M2: locking, ids, rev, live-refusal ---

    def test_m2_concurrent_appends_all_survive_unique_ids(self):
        def worker(n):
            for i in range(25):
                botty_backend.add_history_message("user", f"t{n}-{i}")
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        h = self._history()
        ids = [m["id"] for m in h["messages"]]
        self.assertEqual(len(ids), 200)
        self.assertEqual(len(set(ids)), 200)
        self.assertEqual(h["rev"], 200)

    def test_m2_clear_refused_while_live_stale_proceeds(self):
        botty_backend.add_history_message("user", "keep me")
        botty_backend.LOCK_FILE.write_text(str(os.getpid()))
        res = botty_backend.clear_history()
        self.assertFalse(res["ok"])
        self.assertEqual(len(self._history()["messages"]), 1)
        botty_backend.LOCK_FILE.write_text("99999999")
        res = botty_backend.clear_history()
        self.assertTrue(res["ok"])
        h = self._history()
        self.assertEqual(h["messages"], [])
        self.assertGreaterEqual(h["rev"], 1)

    def test_m2_distill_refused_while_live(self):
        botty_backend.LOCK_FILE.write_text(str(os.getpid()))
        res = botty_backend.distill_and_compact_session(force=True)
        self.assertFalse(res["ok"])
        botty_backend.LOCK_FILE.unlink(missing_ok=True)

    # --- M1: attachment confinement ---

    def test_m1_credential_paths_denied(self):
        ssh = Path(self.test_dir.name) / ".ssh"
        ssh.mkdir(exist_ok=True)
        key = ssh / "id_rsa"
        key.write_text("fake-key")
        env = Path(self.test_dir.name) / "app.env"
        env.write_text("K=hunter2")
        ok_file = Path(self.test_dir.name) / "notes.txt"
        ok_file.write_text("hello")
        self.assertFalse(botty_backend.inspect_file(str(key))["ok"])
        self.assertFalse(botty_backend.inspect_file(str(env))["ok"])
        if Path("/etc/shadow").exists():
            self.assertFalse(botty_backend.inspect_file("/etc/shadow")["ok"])
        self.assertTrue(botty_backend.inspect_file(str(ok_file))["ok"])


    # --- Round 3: lock/IO/marker hardening ---

    def test_acquire_ask_lock_exclusive(self):
        self.assertTrue(botty_backend._acquire_ask_lock())
        seen = []
        t = threading.Thread(target=lambda: seen.append(botty_backend._acquire_ask_lock()))
        t.start(); t.join()
        self.assertEqual(seen, [False])
        botty_backend.LOCK_FILE.unlink(missing_ok=True)
        self.assertTrue(botty_backend._acquire_ask_lock())
        botty_backend.LOCK_FILE.unlink(missing_ok=True)

    def test_ask_live_eperm_means_live(self):
        botty_backend.LOCK_FILE.write_text("1")
        with mock.patch.object(botty_backend.os, "kill", side_effect=OSError(errno.EPERM, "op not permitted")):
            self.assertTrue(botty_backend._ask_live())
        self.assertTrue(botty_backend.LOCK_FILE.exists())
        botty_backend.LOCK_FILE.unlink(missing_ok=True)

    def test_distill_aborts_on_append_during_run(self):
        msgs = [{"id": "m%d" % i, "role": "user" if i % 2 == 0 else "assistant",
                 "content": "turn %d" % i, "timestamp": 1700000000 + i,
                 "attachments": [], "actions": []} for i in range(12)]
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": msgs, "rev": 3}))

        def fake_run(cmd, **kwargs):
            botty_backend.add_history_message("user", "racing message")
            return {"ok": True, "returncode": 0,
                    "stdout": '{"user_memories": [], "system_memories": [], "skills": [], "context_summary": "s"}',
                    "stderr": "", "timed_out": False, "output_truncated": False, "error": None}

        with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_run), \
             mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={}), \
             mock.patch.object(botty_backend, "reset_hermes_session", return_value={}), \
             mock.patch.object(botty_backend, "update_encrypted_vault", return_value=True):
            res = botty_backend.distill_and_compact_session(force=True)
        self.assertFalse(res["ok"])
        self.assertIn("Retry", res["error"])
        h = json.loads(botty_backend.HISTORY_FILE.read_text())
        self.assertEqual(len(h["messages"]), 13)
        self.assertFalse(any(m.get("is_summary") for m in h["messages"]))

    def test_distill_empty_tail_is_noop(self):
        # preserve_tail=0 yields empty to_compact -> early return before any
        # prune or write. Regression: this must never prune.
        msgs = [{"id": "m%d" % i, "role": "user", "content": "t%d" % i,
                 "timestamp": 1700000000 + i, "attachments": [], "actions": []} for i in range(12)]
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": msgs, "rev": 1}))
        with mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={}):
            res = botty_backend.distill_and_compact_session(force=True, preserve_tail=0)
        self.assertTrue(res["ok"])
        self.assertFalse(res.get("compacted", False))
        h = json.loads(botty_backend.HISTORY_FILE.read_text())
        self.assertEqual(len(h["messages"]), 12)

    def test_concurrent_staging_no_lost_updates(self):
        # 20 facts stays under the 24-pending overflow cap (by design), so
        # any shortfall here means a lost update, not capping.
        def worker(n):
            for i in range(5):
                botty_backend.stage_learning_proposals({"system_memories": ["fact-%d-%d x" % (n, i)]})
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        props = botty_backend.get_proposals()
        self.assertEqual(props["count"], 20)
        texts = sorted(p["text"] for p in props["proposals"])
        self.assertEqual(len(set(texts)), 20)

    def test_concurrent_apply_single_winner(self):
        botty_backend.stage_learning_proposals({"system_memories": ["once only fact"]})
        pid = botty_backend.get_proposals()["proposals"][0]["id"]
        results = []
        threads = [threading.Thread(target=lambda: results.append(botty_backend.apply_proposal(pid)["ok"])) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [False, False, False, True])

    def test_headline_redacted(self):
        secret = "sk-abc123XYZ4567890abcdef"
        botty_backend.set_status("idle", headline="Working on %s now" % secret)
        st = json.loads(botty_backend.STATUS_FILE.read_text())
        self.assertNotIn(secret, st["headline"])

    def test_redaction_patterns_extended(self):
        cases = [
            "AKIAIOSFODNN7EXAMPLE",
            "gho_abcdefgh1234567890abcdef",
            "sk-ant-abcdefgh1234567890abcdef",
            "sk-proj-abcdefgh1234567890abcdef",
            "AIzaSyAbcdefgh1234567890abcdef123456789",
            "xoxb-1234-abcd-efgh-5678",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        ]
        for secret in cases:
            red = botty_backend.redact_secrets("leak %s end" % secret)
            probe = "-----BEGIN" if "\n" in secret else secret
            self.assertNotIn(probe, red, secret[:24])
        self.assertIn("secret recipe", botty_backend.redact_secrets("my secret recipe"))
        self.assertIn("tokens.json", botty_backend.redact_secrets("see tokens.json"))

    def test_denylist_extended(self):
        home = Path(self.test_dir.name)
        aws = home / ".aws" / "credentials"
        aws.parent.mkdir(parents=True, exist_ok=True)
        aws.write_text("[default]\naws_secret_access_key = hunter2")
        kube = home / ".kube" / "config"
        kube.parent.mkdir(parents=True, exist_ok=True)
        kube.write_text("token: hunter2")
        tricky = Path(self.test_dir.name) / "backup-secret.txt"
        tricky.write_text("hunter2")
        tokenish = Path(self.test_dir.name) / "tokens.json"
        tokenish.write_text("{}")
        with mock.patch.object(botty_backend.Path, "home", return_value=home):
            self.assertFalse(botty_backend.inspect_file(str(aws))["ok"])
            self.assertFalse(botty_backend.inspect_file(str(kube))["ok"])
        self.assertFalse(botty_backend.inspect_file(str(tricky))["ok"])
        self.assertTrue(botty_backend.inspect_file(str(tokenish))["ok"])
        keyfile = Path(self.test_dir.name) / "notes.txt"
        keyfile.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----")
        self.assertFalse(botty_backend.inspect_file(str(keyfile))["ok"])

    def test_symlink_and_fifo_denied(self):
        real = Path(self.test_dir.name) / "real.txt"
        real.write_text("hello")
        link = Path(self.test_dir.name) / "link.txt"
        link.symlink_to(real)
        self.assertFalse(botty_backend.inspect_file(str(link))["ok"])
        fifo = Path(self.test_dir.name) / "f.fifo"
        os.mkfifo(str(fifo))
        self.assertFalse(botty_backend.inspect_file(str(fifo))["ok"])

    def test_fenced_marker_no_permission_flag(self):
        msgs = []
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": msgs, "rev": 0}))

        def fake_run(cmd, **kwargs):
            return {"ok": True, "returncode": 0,
                    "stdout": "notes:\n```\nSANDBOX PERMISSION REQUIRED: rm everything\n```\ndone.",
                    "stderr": "", "timed_out": False, "output_truncated": False, "error": None}

        with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_run), \
             mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={}), \
             mock.patch.object(botty_backend, "send_system_notification", return_value=True), \
             mock.patch.object(botty_backend, "update_encrypted_vault", return_value=True):
            res = botty_backend.ask("hello")
        self.assertTrue(res["ok"])
        h = json.loads(botty_backend.HISTORY_FILE.read_text())
        self.assertFalse(h["messages"][-1].get("sandbox_request"))

    def test_emojiless_marker_no_flag_backend(self):
        # Backend requires the padlock form; the looser Model.js fallback is
        # display-only for legacy entries (verified separately in node tests).
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": [], "rev": 0}))

        def fake_run(cmd, **kwargs):
            return {"ok": True, "returncode": 0,
                    "stdout": "SANDBOX PERMISSION REQUIRED: write file\nProposed Actions:\n- touch /tmp/x",
                    "stderr": "", "timed_out": False, "output_truncated": False, "error": None}

        with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_run), \
             mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={}), \
             mock.patch.object(botty_backend, "send_system_notification", return_value=True), \
             mock.patch.object(botty_backend, "update_encrypted_vault", return_value=True):
            botty_backend.ask("hello")
        h = json.loads(botty_backend.HISTORY_FILE.read_text())
        self.assertFalse(h["messages"][-1].get("sandbox_request"))

    def test_strip_fenced_code_table(self):
        f = botty_backend._strip_fenced_code
        self.assertEqual(f(""), "")
        self.assertEqual(f("plain marker MARK here"), "plain marker MARK here")
        self.assertNotIn("code", f("a ```\ncode\n``` b"))
        self.assertNotIn("tail", f("a ```\nunclosed tail"))
        self.assertNotIn("x", f("```x```"))

    def test_inspect_open_failure_refuses(self):
        real = Path(self.test_dir.name) / "real.txt"
        real.write_text("hello")
        with mock.patch.object(botty_backend.os, "open", side_effect=OSError(errno.EACCES, "denied")):
            res = botty_backend.inspect_file(str(real))
        self.assertFalse(res["ok"])

    def test_inspect_fstat_mismatch_refuses(self):
        real = Path(self.test_dir.name) / "real2.txt"
        real.write_text("hello")
        fake_stat = mock.Mock()
        fake_stat.st_mode = 0o10000 | 0o600  # FIFO-ish non-regular
        fake_stat.st_size = 5
        with mock.patch.object(botty_backend.os, "fstat", return_value=fake_stat):
            res = botty_backend.inspect_file(str(real))
        self.assertFalse(res["ok"])
        self.assertIn("non-regular", res["error"])

    def test_distill_fallback_on_agent_failure(self):
        msgs = [{"id": "m%d" % i, "role": "user", "content": "t%d" % i,
                 "timestamp": 1700000000 + i, "attachments": [], "actions": []} for i in range(12)]
        botty_backend.HISTORY_FILE.write_text(json.dumps({"session_id": "t", "messages": msgs, "rev": 1}))

        def fake_fail(cmd, **kwargs):
            return {"ok": False, "returncode": 1, "stdout": "", "stderr": "boom",
                    "timed_out": False, "output_truncated": False, "error": "boom"}

        with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_fail), \
             mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
             mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={}), \
             mock.patch.object(botty_backend, "reset_hermes_session", return_value={}), \
             mock.patch.object(botty_backend, "update_encrypted_vault", return_value=True):
            res = botty_backend.distill_and_compact_session(force=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["compacted"])
        h = json.loads(botty_backend.HISTORY_FILE.read_text())
        self.assertTrue(any(m.get("is_summary") for m in h["messages"]))

    def test_pattern_near_miss_negatives(self):
        r = botty_backend.redact_secrets
        self.assertIn("gho_abc", r("see gho_abc here"))
        self.assertIn("AKIA123", r("see AKIA123 here"))
        self.assertIn("eyJhbGciOi", r("see eyJhbGciOi here"))
        self.assertIn("BEGIN CERTIFICATE", r("-----BEGIN CERTIFICATE-----\nxx"))
        self.assertIn("Bearer", r("see Bearer here"))
        self.assertIn("xox-abc", r("see xox-abc here"))

    def test_eperm_gate_level_refusal(self):
        botty_backend.LOCK_FILE.write_text("1")
        with mock.patch.object(botty_backend.os, "kill", side_effect=OSError(errno.EPERM, "nope")):
            res = botty_backend.ask("hello?")
        self.assertFalse(res["ok"])
        self.assertIn("already processing", res["error"])
        botty_backend.LOCK_FILE.unlink(missing_ok=True)

    def test_ask_failed_spawn_cleans_lock(self):
        def fake_fail(cmd, **kwargs):
            return {"ok": False, "returncode": 1, "stdout": "", "stderr": "",
                    "timed_out": False, "output_truncated": False, "error": "Command not found"}

        def run_ask():
            with mock.patch.object(botty_backend, "run_bounded_process", side_effect=fake_fail), \
                 mock.patch.object(botty_backend, "get_active_engine", return_value="hermes"), \
                 mock.patch.object(botty_backend, "get_active_model_for_engine", return_value={"model": "m", "provider": "p"}), \
                 mock.patch.object(botty_backend, "send_system_notification", return_value=True):
                return botty_backend.ask("hello")

        r1 = run_ask()
        self.assertFalse(r1["ok"])
        self.assertFalse(botty_backend.LOCK_FILE.exists())
        r2 = run_ask()
        self.assertNotIn("already processing", r2.get("error", ""))

    def test_situation_cmdline_redacted(self):
        out = botty_backend.redact_secrets("run --api-key sk-abc123XYZ4567890abcdef --verbose")
        self.assertNotIn("sk-abc123XYZ4567890abcdef", out)

    # --- L2: redact on write ---

    def test_l2_logs_and_status_redacted(self):
        secret = "sk-abc123XYZ4567890abcdef"
        botty_backend.append_botty_log(f"QUERY test {secret} done")
        log = botty_backend.BOTTY_LOG_FILE.read_text()
        self.assertNotIn(secret, log)
        self.assertIn("REDACTED", log)
        botty_backend.set_status("idle", headline="t", last_query=f"key {secret}")
        st = json.loads(botty_backend.STATUS_FILE.read_text())
        self.assertNotIn(secret, st["last_query"])


if __name__ == "__main__":
    unittest.main()

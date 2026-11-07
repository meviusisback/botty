"""Regression tests for honest Clear Chat + fail-closed ask (marketplace finding #13663).

Isolates all module paths into a temp dir (same pattern as test_security.py /
test_compaction.py) so no real user data is touched, and stubs
reset_hermes_session() so no real Hermes state.db is ever opened.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import botty_backend


class TestClearHistoryHonestReset(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.test_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_mem_dir = Path(self.test_dir.name) / "memories"
        self.hermes_mem_dir.mkdir(parents=True, exist_ok=True)

        self.orig = {}
        for name in ("BOTTY_DATA_DIR", "HISTORY_FILE", "HISTORY_ARCHIVE_FILE",
                     "CONFIG_FILE", "STATUS_FILE", "LOCK_FILE", "HISTORY_LOCK_FILE",
                     "HERMES_MEMORY_DIR", "HERMES_SKILLS_DIR", "HERMES_STATE_DB",
                     "PROPOSALS_FILE", "BOTTY_LOG_FILE", "CONTEXT_BRIDGE_FILE",
                     "VAULT_FILE", "APPROVED_STAGING_DIR",
                     "COMPACT_LOCK_FILE", "COMPACTION_LOG_FILE", "COMPACTION_STATE_FILE",
                     "VOICE_PID_FILE", "SCREENSHOT_DIR"):
            self.orig[name] = getattr(botty_backend, name)
        botty_backend.BOTTY_DATA_DIR = self.data_dir
        botty_backend.HISTORY_FILE = self.data_dir / "history.json"
        botty_backend.HISTORY_ARCHIVE_FILE = self.data_dir / "history_archive.jsonl"
        botty_backend.CONFIG_FILE = self.data_dir / "config.json"
        botty_backend.STATUS_FILE = self.data_dir / "status.json"
        botty_backend.LOCK_FILE = self.data_dir / "running.pid"
        botty_backend.HISTORY_LOCK_FILE = self.data_dir / ".history.lock"
        botty_backend.HERMES_MEMORY_DIR = self.hermes_mem_dir
        botty_backend.HERMES_SKILLS_DIR = Path(self.test_dir.name) / "skills"
        botty_backend.HERMES_STATE_DB = Path(self.test_dir.name) / "state.db"
        botty_backend.PROPOSALS_FILE = self.data_dir / "pending_proposals.json"
        botty_backend.BOTTY_LOG_FILE = self.data_dir / "botty.log"
        botty_backend.CONTEXT_BRIDGE_FILE = self.data_dir / "context_bridge.txt"
        botty_backend.VAULT_FILE = self.data_dir / "vault.enc"
        botty_backend.APPROVED_STAGING_DIR = self.data_dir / "approved_proposals"
        botty_backend.APPROVED_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        botty_backend.COMPACT_LOCK_FILE = self.data_dir / "compacting.pid"
        botty_backend.COMPACTION_LOG_FILE = self.data_dir / "compaction.log"
        botty_backend.COMPACTION_STATE_FILE = self.data_dir / "compaction_state.json"
        botty_backend.VOICE_PID_FILE = self.data_dir / "voice.pid"
        botty_backend.SCREENSHOT_DIR = self.data_dir / "captures"

        # A conversation the user is about to wipe.
        self._write_json(botty_backend.HISTORY_FILE, {
            "session_id": "botty-widget",
            "messages": [
                {"role": "user", "content": "private question"},
                {"role": "assistant", "content": "private answer"},
            ],
            "rev": 3,
        })
        botty_backend.CONTEXT_BRIDGE_FILE.write_text(
            "SUMMARY of the conversation being cleared", encoding="utf-8")
        # No reset_pending on purpose: the common case is a clear with no
        # deferred compaction, so the fence must ARM the retry itself (the
        # mutation sweep's "fence arms reset_pending" guard is masked when
        # the seed already contains the flag).
        self._write_json(botty_backend.COMPACTION_STATE_FILE,
                         {"reset_bridge": "old summary", "clear_epoch": 7})

    def tearDown(self):
        for name, val in self.orig.items():
            setattr(botty_backend, name, val)
        self.test_dir.cleanup()

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _write_json(path, obj):
        Path(path).write_text(json.dumps(obj), encoding="utf-8")

    def _state(self):
        return json.loads(botty_backend.COMPACTION_STATE_FILE.read_text())

    def _history(self):
        return json.loads(botty_backend.HISTORY_FILE.read_text())

    @staticmethod
    def _fail_reset():
        return {"ok": False, "error": "database is locked"}

    @staticmethod
    def _ok_reset():
        return {"ok": True, "reset": True, "new_title": "archived-botty-widget-1"}

    def _clear_with(self, result):
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=result) as m:
            res = botty_backend.clear_history()
        return res, m

    # -- clear_history: honest report + armed retry -------------------------

    def test_clear_history_reset_failure_reports_and_arms_retry(self):
        res, m = self._clear_with(self._fail_reset())
        m.assert_called_once_with("botty-widget")

        # Honest report — the old code returned ok/True "session reset" here.
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("history_cleared"))
        self.assertFalse(res.get("session_reset"))
        self.assertIn("database is locked", res.get("error", ""))
        self.assertIn("message", res)  # QML-visible key

        # Visible history: wiped, but the failure is in the chat the user reads.
        msgs = self._history()["messages"]
        self.assertEqual([x["role"] for x in msgs], ["assistant"])
        self.assertIn("session reset failed", msgs[0]["content"])
        self.assertNotIn("private question", json.dumps(msgs))

        # State: fenced + armed (bridge-less) + ask guard, wiped bridge gone.
        st = self._state()
        self.assertTrue(st.get("reset_pending"))
        self.assertTrue(st.get("clear_reset_pending"))
        self.assertNotIn("reset_bridge", st)
        self.assertEqual(st.get("clear_epoch"), 8)  # seeded 7, bumped once
        self.assertFalse(botty_backend.CONTEXT_BRIDGE_FILE.exists())

        # Early return: the idle status must not have overwritten the error.
        status = botty_backend.get_status()
        self.assertEqual(status.get("state"), "error")
        self.assertIn("session reset failed", status.get("last_error", ""))

    def test_clear_history_reset_success_clears_retry_state(self):
        res, m = self._clear_with(self._ok_reset())
        m.assert_called_once_with("botty-widget")

        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("message"), "History cleared and session reset")

        st = self._state()
        self.assertNotIn("reset_pending", st)
        self.assertNotIn("reset_bridge", st)
        self.assertNotIn("clear_reset_pending", st)
        self.assertEqual(st.get("clear_epoch"), 8)

        msgs = self._history()["messages"]
        self.assertEqual(msgs, [])  # no failure banner on the happy path
        self.assertFalse(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        self.assertEqual(botty_backend.get_status().get("state"), "idle")

    # -- the armed retry actually closes the harm ---------------------------

    def test_apply_pending_reset_retries_after_clear_failure(self):
        self._clear_with(self._fail_reset())
        self.assertTrue(botty_backend._clear_reset_incomplete())

        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._ok_reset()):
            applied = botty_backend._apply_pending_reset()
        self.assertTrue(applied)

        st = self._state()
        self.assertNotIn("reset_pending", st)
        self.assertNotIn("clear_reset_pending", st)
        # Bridge-less retry: the wiped bridge must NOT come back.
        self.assertFalse(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        self.assertFalse(botty_backend._clear_reset_incomplete())

    def test_clear_reset_flag_cleared_on_success(self):
        self._clear_with(self._fail_reset())
        self.assertTrue(botty_backend._clear_reset_incomplete())
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._ok_reset()):
            botty_backend._apply_pending_reset()
        # Availability restored: the next ask is not refused.
        self.assertFalse(botty_backend._clear_reset_incomplete())

    # -- fail-closed ask ----------------------------------------------------

    def test_ask_refused_while_clear_reset_pending(self):
        self._clear_with(self._fail_reset())

        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "run_bounded_process",
                               side_effect=AssertionError("spawned hermes")), \
             mock.patch.object(botty_backend, "_consume_approved_proposal") as consume, \
             mock.patch.object(botty_backend, "_resolve_approved_proposal") as resolve:
            res = botty_backend._ask_body("hello there")

        self.assertFalse(res.get("ok"))
        self.assertIn("session reset", res.get("error", "").lower())
        # No spawn (patched to explode), and the placement invariant: the
        # refusal happens before the user message is persisted and before any
        # approved card is consumed.
        consume.assert_not_called()
        resolve.assert_not_called()
        roles = [m["role"] for m in self._history()["messages"]]
        self.assertNotIn("user", roles)
        # The refusal itself is visible in the chat (the _ask_refused contract).
        self.assertIn("assistant", roles)

        # Bypass run: same guard, still no card consumed.
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "run_bounded_process",
                               side_effect=AssertionError("spawned hermes")), \
             mock.patch.object(botty_backend, "_consume_approved_proposal") as consume, \
             mock.patch.object(botty_backend, "_resolve_approved_proposal") as resolve:
            res2 = botty_backend._ask_body("hello", bypass_sandbox=True,
                                           approve_id="card-1")
        self.assertFalse(res2.get("ok"))
        consume.assert_not_called()
        resolve.assert_not_called()

    def test_ask_slot_free_after_guard_refusal(self):
        self._clear_with(self._fail_reset())
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "run_bounded_process",
                               side_effect=AssertionError("spawned hermes")):
            botty_backend._ask_body("hello")
        # A missed _release_ask_lock() would leave running.pid live and wedge
        # every future ask behind "already processing".
        self.assertTrue(botty_backend._acquire_ask_lock())
        botty_backend._release_ask_lock()

    def test_clear_reset_incomplete_takes_history_flock(self):
        events = []
        orig_acq = botty_backend._exclusive_file_lock
        orig_rel = botty_backend._release_file_lock
        orig_load = botty_backend.load_json_file

        def acq():
            events.append("acquire")
            return orig_acq()

        def rel(fh):
            events.append("release")
            orig_rel(fh)

        def load(path, default=None):
            events.append("read")
            return orig_load(path, default)

        with mock.patch.object(botty_backend, "_exclusive_file_lock", side_effect=acq), \
             mock.patch.object(botty_backend, "_release_file_lock", side_effect=rel), \
             mock.patch.object(botty_backend, "load_json_file", side_effect=load):
            botty_backend._clear_reset_incomplete()

        # The read is bracketed by the flock: without it the read is unordered
        # against a clear_history mid-reset (round-3 P1 interleaving).
        self.assertEqual(events, ["acquire", "read", "release"])

    # -- the compactor must not spend a reset it did not succeed at  --------

    def _run_finalize(self, result):
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=result) as m:
            fh = botty_backend._exclusive_file_lock()
            botty_backend._lock_state.held = True
            try:
                botty_backend._finalize_compaction_reset()
            finally:
                botty_backend._lock_state.held = False
                botty_backend._release_file_lock(fh)
        return m

    def test_compaction_finalize_keeps_flags_armed_on_reset_failure(self):
        # The #13663 review-round-2 P0 scenario: a Clear Chat already armed
        # the ask guard, then a Compact runs and its own reset fails too.
        self._write_json(botty_backend.COMPACTION_STATE_FILE,
                         {"reset_pending": True, "reset_bridge": "old summary",
                          "clear_epoch": 7, "clear_reset_pending": True})
        self._run_finalize(self._fail_reset())
        st = self._state()
        self.assertTrue(st.get("reset_pending"))
        self.assertTrue(st.get("clear_reset_pending"))
        # Stale deferred summary dropped: the fresh bridge (written straight to
        # CONTEXT_BRIDGE_FILE by this branch) must not be overwritten by it.
        self.assertNotIn("reset_bridge", st)

    def test_compaction_finalize_success_spends_flags(self):
        self._run_finalize(self._ok_reset())
        st = self._state()
        self.assertNotIn("reset_pending", st)
        self.assertNotIn("reset_bridge", st)
        self.assertNotIn("clear_reset_pending", st)

    def test_compaction_finalize_requires_history_flock(self):
        # Fail loud instead of a silent lost update for a lockless caller —
        # a real RuntimeError, not an assert (asserts vanish under python -O).
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._ok_reset()):
            self.assertFalse(getattr(botty_backend._lock_state, "held", False))
            with self.assertRaises(RuntimeError):
                botty_backend._finalize_compaction_reset()

    def test_compaction_finalize_arms_retry_without_preexisting_pending(self):
        # Normal compaction (no prior defer, no clear): a failed reset must arm
        # reset_pending so the committed prune's session reset is retried —
        # this pins the ARM line itself (seeding reset_pending would mask it).
        self._write_json(botty_backend.COMPACTION_STATE_FILE, {})
        self._run_finalize(self._fail_reset())
        st = self._state()
        self.assertTrue(st.get("reset_pending"))
        self.assertNotIn("reset_bridge", st)
        self.assertNotIn("clear_reset_pending", st)

    def test_compactor_nondeferred_branch_wires_finalize(self):
        # The gate only matters if the compactor branch actually calls it:
        # reverting the call site to the old ungated inline pair must fail here
        # (integration-level pin; the unit tests call the helper directly).
        src = Path(botty_backend.__file__).read_text(encoding="utf-8")
        self.assertIn("\n                _finalize_compaction_reset()", src)
        self.assertNotIn("\n                reset_hermes_session(HERMES_SESSION_NAME)", src)

    # -- review round-1 fixes (P1 + P2s) ------------------------------------

    def test_clear_history_arm_write_failure_changes_nothing(self):
        # P1: if the guard cannot be armed, NOTHING may be wiped and the CLI
        # must still get an honest JSON dict (no escaping exception).
        real_save = botty_backend.save_json_file

        def failing_save(path, data):
            if Path(path) == botty_backend.COMPACTION_STATE_FILE:
                raise OSError("No space left on device")
            return real_save(path, data)

        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "save_json_file", side_effect=failing_save):
            res = botty_backend.clear_history()   # must not raise

        self.assertFalse(res.get("ok"))
        self.assertFalse(res.get("history_cleared"))
        self.assertFalse(res.get("session_reset"))
        self.assertIn("message", res)
        # Nothing changed: chat intact, bridge intact, guard never armed.
        msgs = self._history()["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "private question")
        self.assertTrue(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        st = self._state()
        self.assertNotIn("reset_pending", st)
        self.assertNotIn("clear_reset_pending", st)
        self.assertEqual(st.get("clear_epoch"), 7)   # arm never landed
        # The badge must move: the widget reads no JSON (round-2 QML finding).
        self.assertEqual(botty_backend.get_status().get("state"), "error")

    def test_clear_history_wipe_failure_rolls_back_guard(self):
        # P1 sibling: the wipe itself fails after the guard was armed — the
        # PRE-ARM state is restored verbatim (no guard when there was none),
        # and the failure is reported honestly with a visible badge.
        real_save = botty_backend.save_json_file

        def failing_save(path, data):
            if Path(path) == botty_backend.HISTORY_FILE:
                raise OSError("No space left on device")
            return real_save(path, data)

        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "save_json_file", side_effect=failing_save):
            res = botty_backend.clear_history()   # must not raise

        self.assertFalse(res.get("ok"))
        self.assertFalse(res.get("history_cleared"))
        msgs = self._history()["messages"]
        self.assertEqual(len(msgs), 2)             # wipe never landed
        self.assertTrue(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        st = self._state()
        self.assertNotIn("reset_pending", st)      # pre-arm state restored
        self.assertNotIn("clear_reset_pending", st)
        self.assertEqual(st.get("clear_epoch"), 7)  # arm's bump rolled back
        self.assertEqual(botty_backend.get_status().get("state"), "error")

    def test_clear_history_wipe_failure_restores_preexisting_guard(self):
        # Round-2 P1: a RETRY clear whose wipe fails must leave the guard an
        # EARLIER failed clear armed — and its deferred compaction retry and
        # epoch — exactly as they were. A blanket clear would disarm them
        # while history is already wiped: the next ask would resume the
        # cleared session (#13663).
        self._write_json(botty_backend.COMPACTION_STATE_FILE,
                         {"reset_pending": True, "reset_bridge": "deferred summary",
                          "clear_epoch": 7, "clear_reset_pending": True})
        snapshot = self._state()
        real_save = botty_backend.save_json_file

        def failing_save(path, data):
            if Path(path) == botty_backend.HISTORY_FILE:
                raise OSError("No space left on device")
            return real_save(path, data)

        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._fail_reset()), \
             mock.patch.object(botty_backend, "save_json_file", side_effect=failing_save):
            res = botty_backend.clear_history()   # must not raise

        self.assertFalse(res.get("ok"))
        self.assertFalse(res.get("history_cleared"))
        self.assertEqual(self._state(), snapshot)  # byte-for-byte semantics
        self.assertTrue(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        msgs = self._history()["messages"]
        self.assertEqual(len(msgs), 2)

    def test_clear_history_corrupt_history_rev_never_escapes(self):
        # Round-2 P2: old_rev is parsed BEFORE the arm and defensively — a
        # corrupt rev must not raise after the guard is armed (CLI would
        # print no JSON). Wiping replaces the file wholesale, so rev starts 0.
        self._write_json(botty_backend.HISTORY_FILE,
                         {"session_id": "botty-widget", "rev": "not-a-number",
                          "messages": [{"role": "user", "content": "hi"}]})
        with mock.patch.object(botty_backend, "reset_hermes_session",
                               return_value=self._ok_reset()):
            res = botty_backend.clear_history()   # must not raise
        self.assertTrue(res.get("ok"), res)
        hist = self._history()                    # wiped and parseable again
        self.assertEqual(hist["messages"], [])
        self.assertEqual(self._state().get("clear_epoch"), 8)

    def test_ask_guard_runs_after_ask_lock(self):
        # Ordering IS the race fix: a clear that starts between a pre-lock
        # guard read and lock acquisition would arm the flag under us. Pin the
        # call order (and the release-before-refusal) so moving the guard
        # above _acquire_ask_lock fails here.
        events = []

        def acquire():
            events.append("acquire")
            return True

        def incomplete():
            events.append("incomplete")
            return True    # guard fires -> refusal, no spawn

        def release():
            events.append("release")

        with mock.patch.object(botty_backend, "_acquire_ask_lock", side_effect=acquire), \
             mock.patch.object(botty_backend, "_clear_reset_incomplete",
                               side_effect=incomplete), \
             mock.patch.object(botty_backend, "_release_ask_lock", side_effect=release), \
             mock.patch.object(botty_backend, "run_bounded_process",
                               side_effect=AssertionError("spawned hermes")):
            res = botty_backend._ask_body("hello")

        self.assertFalse(res.get("ok"))
        self.assertEqual(events, ["acquire", "incomplete", "release"])

    def test_clear_reset_incomplete_reentrant_when_lock_held(self):
        # If the caller already holds the flock, taking a SECOND descriptor
        # would self-deadlock (flock is per open file description): the helper
        # must read directly instead of re-locking.
        events = []
        orig_acq = botty_backend._exclusive_file_lock
        orig_rel = botty_backend._release_file_lock
        orig_load = botty_backend.load_json_file

        def acq():
            events.append("acquire")
            return orig_acq()

        def rel(fh):
            events.append("release")
            orig_rel(fh)

        def load(path, default=None):
            events.append("read")
            return orig_load(path, default)

        self._write_json(botty_backend.COMPACTION_STATE_FILE,
                         {"clear_reset_pending": True})
        botty_backend._lock_state.held = True   # simulate caller holding it
        try:
            with mock.patch.object(botty_backend, "_exclusive_file_lock",
                                   side_effect=acq), \
                 mock.patch.object(botty_backend, "_release_file_lock",
                                   side_effect=rel), \
                 mock.patch.object(botty_backend, "load_json_file", side_effect=load):
                result = botty_backend._clear_reset_incomplete()
        finally:
            botty_backend._lock_state.held = False

        self.assertTrue(result)
        self.assertEqual(events, ["read"])   # no second flock descriptor


if __name__ == "__main__":
    unittest.main()

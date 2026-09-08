#!/usr/bin/env python3
"""
Test suite for Botty Memory Distillation & Context Compaction.
Tests:
- SQLite Hermes session resetting
- Memory addition, parsing, and duplicate suppression
- Skill creation and SKILL.md structure
- Context compaction, history pruning, and archive persistence
- Threshold and tail preservation logic
- Clear history synchronization
- CONTINUITY BRIDGE: distilled context must survive a Hermes session reset and be
  re-injected into the next ask() prompt (regression: agent lost the thread after
  compaction and asked the user to re-explain the task).
"""

import os
import sys
import json
import time
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import backend module under test
import botty_backend

class TestCompactionAndDistillation(unittest.TestCase):
    def setUp(self):
        # Set up isolated temporary directory for test data
        self.test_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.test_dir.name) / ".local" / "share" / "botty"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.hermes_dir = Path(self.test_dir.name) / ".hermes" / "profiles" / "botty"
        self.hermes_mem_dir = self.hermes_dir / "memories"
        self.hermes_skills_dir = self.hermes_dir / "skills"
        self.hermes_mem_dir.mkdir(parents=True, exist_ok=True)
        self.hermes_skills_dir.mkdir(parents=True, exist_ok=True)
        
        self.history_file = self.data_dir / "history.json"
        self.history_archive_file = self.data_dir / "history_archive.jsonl"
        self.config_file = self.data_dir / "config.json"
        self.status_file = self.data_dir / "status.json"
        self.hermes_state_db = self.hermes_dir / "state.db"
        self.proposals_file = self.data_dir / "pending_proposals.json"
        self.context_bridge_file = self.data_dir / "context_bridge.txt"

        # Patch module-level paths
        self.orig_data_dir = botty_backend.BOTTY_DATA_DIR
        self.orig_history_file = botty_backend.HISTORY_FILE
        self.orig_archive_file = botty_backend.HISTORY_ARCHIVE_FILE
        self.orig_config_file = botty_backend.CONFIG_FILE
        self.orig_status_file = botty_backend.STATUS_FILE
        self.orig_lock_file = botty_backend.LOCK_FILE
        self.orig_hermes_mem_dir = botty_backend.HERMES_MEMORY_DIR
        self.orig_hermes_skills_dir = botty_backend.HERMES_SKILLS_DIR
        self.orig_hermes_state_db = botty_backend.HERMES_STATE_DB
        self.orig_proposals_file = botty_backend.PROPOSALS_FILE
        self.orig_context_bridge_file = botty_backend.CONTEXT_BRIDGE_FILE

        botty_backend.BOTTY_DATA_DIR = self.data_dir
        botty_backend.HISTORY_FILE = self.history_file
        botty_backend.HISTORY_ARCHIVE_FILE = self.history_archive_file
        botty_backend.CONFIG_FILE = self.config_file
        botty_backend.STATUS_FILE = self.status_file
        botty_backend.LOCK_FILE = self.data_dir / "running.pid"
        botty_backend.HERMES_MEMORY_DIR = self.hermes_mem_dir
        botty_backend.HERMES_SKILLS_DIR = self.hermes_skills_dir
        botty_backend.HERMES_STATE_DB = self.hermes_state_db
        botty_backend.PROPOSALS_FILE = self.proposals_file
        botty_backend.CONTEXT_BRIDGE_FILE = self.context_bridge_file

        # Initialize mock SQLite state.db with sessions table
        con = sqlite3.connect(str(self.hermes_state_db))
        cur = con.cursor()
        cur.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, created_at INTEGER)")
        cur.execute("INSERT INTO sessions VALUES ('sess_123', 'botty-widget', 1700000000)")
        con.commit()
        con.close()

    def tearDown(self):
        # Restore module-level paths
        botty_backend.BOTTY_DATA_DIR = self.orig_data_dir
        botty_backend.HISTORY_FILE = self.orig_history_file
        botty_backend.HISTORY_ARCHIVE_FILE = self.orig_archive_file
        botty_backend.CONFIG_FILE = self.orig_config_file
        botty_backend.STATUS_FILE = self.orig_status_file
        botty_backend.LOCK_FILE = self.orig_lock_file
        botty_backend.HERMES_MEMORY_DIR = self.orig_hermes_mem_dir
        botty_backend.HERMES_SKILLS_DIR = self.orig_hermes_skills_dir
        botty_backend.HERMES_STATE_DB = self.orig_hermes_state_db
        botty_backend.PROPOSALS_FILE = self.orig_proposals_file
        botty_backend.CONTEXT_BRIDGE_FILE = self.orig_context_bridge_file
        self.test_dir.cleanup()

    def test_threshold_config(self):
        self.assertEqual(botty_backend.get_auto_compact_threshold(), 14)
        self.assertEqual(botty_backend.get_compact_preserve_tail(), 4)

        # Write custom config
        self.config_file.write_text(json.dumps({
            "auto_compaction_threshold": 20,
            "compact_preserve_tail": 6
        }))
        self.assertEqual(botty_backend.get_auto_compact_threshold(), 20)
        self.assertEqual(botty_backend.get_compact_preserve_tail(), 6)

    def test_request_timeout_config_defaults_and_overrides(self):
        self.assertEqual(botty_backend.get_request_timeout_seconds(), 600)

        self.config_file.write_text(json.dumps({"request_timeout_seconds": 901}))
        self.assertEqual(botty_backend.get_request_timeout_seconds(), 901)

    def test_request_timeout_config_rejects_invalid_values(self):
        self.config_file.write_text(json.dumps({"request_timeout_seconds": "not-a-number"}))
        self.assertEqual(botty_backend.get_request_timeout_seconds(), 600)

        self.config_file.write_text(json.dumps({"request_timeout_seconds": 0}))
        self.assertEqual(botty_backend.get_request_timeout_seconds(), 600)

    def test_request_timeout_is_passed_to_agent_and_error_text_matches(self):
        self.config_file.write_text(json.dumps({"request_timeout_seconds": 901}))
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout_s"] = kwargs.get("timeout_s")
            return {"ok": True, "returncode": None, "stdout": "", "stderr": "",
                    "timed_out": True, "output_truncated": False, "error": None}

        with patch("botty_backend.set_status"), \
             patch("botty_backend.append_botty_log"), \
             patch("botty_backend.add_history_message"), \
             patch("botty_backend.send_system_notification"), \
             patch("botty_backend.get_active_engine", return_value="hermes"), \
             patch("botty_backend.get_active_model_for_engine", return_value={"model": "test", "provider": "test"}), \
             patch("botty_backend.get_sandbox_mode", return_value=False), \
             patch("botty_backend.run_bounded_process", side_effect=fake_run):
            result = botty_backend.ask("hello")

        self.assertEqual(captured["timeout_s"], 901)
        self.assertEqual(result, {"ok": False, "error": "Request timed out after 901 seconds."})
        # argv must not carry prompt content (F3): query rides in the query file.
        self.assertNotIn("hello", captured["cmd"])

    def test_reset_hermes_session(self):
        # Check initial state
        con = sqlite3.connect(str(self.hermes_state_db))
        cur = con.cursor()
        cur.execute("SELECT title FROM sessions WHERE id = 'sess_123'")
        self.assertEqual(cur.fetchone()[0], "botty-widget")
        con.close()

        res = botty_backend.reset_hermes_session("botty-widget")
        self.assertTrue(res["ok"])
        self.assertTrue(res["reset"])

        # Check archived title in database
        con = sqlite3.connect(str(self.hermes_state_db))
        cur = con.cursor()
        cur.execute("SELECT title FROM sessions WHERE id = 'sess_123'")
        title = cur.fetchone()[0]
        self.assertTrue(title.startswith("archived-botty-widget-"))
        con.close()

    def test_add_and_get_memories(self):
        res1 = botty_backend.add_memory("Workstation uses Hyprland on Wayland", is_user_fact=False)
        self.assertTrue(res1["ok"])

        res2 = botty_backend.add_memory("User prefers concise git commit messages", is_user_fact=True)
        self.assertTrue(res2["ok"])

        mems = botty_backend.get_memories()
        self.assertEqual(mems["count"], 2)
        sys_mems = [m for m in mems["memories"] if m["type"] == "system"]
        user_mems = [m for m in mems["memories"] if m["type"] == "user"]
        
        self.assertEqual(len(sys_mems), 1)
        self.assertIn("Hyprland", sys_mems[0]["text"])
        self.assertEqual(len(user_mems), 1)
        self.assertIn("concise git commit", user_mems[0]["text"])

    def test_create_and_get_skills(self):
        res = botty_backend.create_skill(
            name="hyprland-monitors",
            description="Manage and query Hyprland monitors",
            instructions="Run `hyprctl monitors` to list active display outputs."
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "hyprland-monitors")

        skill_file = self.hermes_skills_dir / "hyprland-monitors" / "SKILL.md"
        self.assertTrue(skill_file.exists())
        content = skill_file.read_text(encoding="utf-8")
        self.assertIn("name: hyprland-monitors", content)
        self.assertIn("hyprctl monitors", content)

        skills = botty_backend.get_skills()
        self.assertEqual(skills["count"], 1)
        self.assertEqual(skills["skills"][0]["name"], "hyprland-monitors")

    def test_distill_and_compact_session(self):
        # Create 16 messages in history
        messages = []
        for i in range(16):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({
                "id": f"msg_{i}",
                "role": role,
                "content": f"Turn {i} content: User discussing Omarchy setup and Hyprland config.",
                "timestamp": 1700000000 + i,
                "attachments": [],
                "actions": []
            })
        self.history_file.write_text(json.dumps({"session_id": "botty-widget", "messages": messages}))

        # Mock LLM distillation output
        mock_llm_json = json.dumps({
            "user_memories": ["User prefers Python 3.11 for CLI tools"],
            "system_memories": ["Hyprland socket is located at $XDG_RUNTIME_DIR/hypr"],
            "skills": [
                {
                    "name": "audio-restart",
                    "description": "Restart Pipewire audio subsystem",
                    "instructions": "Use the desktop audio settings to restart the audio service."
                }
            ],
            "context_summary": "Discussed Omarchy audio and window management configurations."
        })

        def fake_run(cmd, **kwargs):
            return {"ok": True, "returncode": 0,
                    "stdout": f"Here is the distillation:\n```json\n{mock_llm_json}\n```",
                    "stderr": "", "timed_out": False, "output_truncated": False, "error": None}

        with patch("botty_backend.run_bounded_process", side_effect=fake_run):
            res = botty_backend.distill_and_compact_session(force=False, preserve_tail=4)

        self.assertTrue(res["ok"])
        self.assertTrue(res["compacted"])
        self.assertEqual(res["compacted_count"], 12) # 16 - 4
        self.assertEqual(res["remaining_count"], 5)  # 1 summary + 4 tail

        # Verify history.json content
        updated_history = json.loads(self.history_file.read_text())
        msgs = updated_history["messages"]
        self.assertEqual(len(msgs), 5)
        self.assertTrue(msgs[0].get("is_summary"))
        self.assertIn("Discussed Omarchy audio", msgs[0]["content"])
        self.assertEqual(msgs[1]["id"], "msg_12") # First preserved tail message
        self.assertEqual(msgs[4]["id"], "msg_15") # Last preserved tail message

        # Verify archive file
        self.assertTrue(self.history_archive_file.exists())
        archive_lines = self.history_archive_file.read_text().strip().splitlines()
        self.assertEqual(len(archive_lines), 12)

        # F2: compaction must NOT auto-write memories/skills — they are staged
        # as proposals awaiting explicit per-entry user review.
        mems = botty_backend.get_memories()
        self.assertEqual(mems["count"], 0, "no memory may be auto-written by compaction")
        skills = botty_backend.get_skills()
        self.assertEqual(skills["count"], 0, "no skill may be auto-created by compaction")

        props = botty_backend.get_proposals()
        self.assertEqual(props["count"], 3)
        by_kind = {}
        for p in props["proposals"]:
            by_kind[(p["kind"], p.get("is_user_fact"))] = p
        self.assertEqual(by_kind[("memory", True)]["text"], "User prefers Python 3.11 for CLI tools")
        self.assertEqual(by_kind[("memory", False)]["text"], "Hyprland socket is located at $XDG_RUNTIME_DIR/hypr")
        self.assertEqual(by_kind[("skill", None)]["name"], "audio-restart")
        # Nothing applied until explicit approval
        self.assertEqual(res["memories_added"], 0)
        self.assertEqual(res["skills_added"], 0)
        self.assertEqual(res["proposals_staged"], 3)

        # Explicit per-entry approval writes the memory to the live store.
        mem_prop = by_kind[("memory", True)]["id"]
        apply_res = botty_backend.apply_proposal(mem_prop)
        self.assertTrue(apply_res["ok"])
        mems = botty_backend.get_memories()
        self.assertEqual(mems["count"], 1)
        self.assertIn("Python 3.11", mems["memories"][0]["text"])

        # Skill stays staged until explicitly approved.
        skills = botty_backend.get_skills()
        self.assertEqual(skills["count"], 0)

    def test_compaction_writes_continuity_bridge(self):
        """Compaction must persist a context bridge so the model keeps prior
        context even after reset_hermes_session() renames the SQLite session."""
        messages = []
        for i in range(16):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({
                "id": f"msg_{i}",
                "role": role,
                "content": f"Turn {i}: configuring K380 keyboard systemd service.",
                "timestamp": 1700000000 + i,
                "attachments": [],
                "actions": []
            })
        self.history_file.write_text(json.dumps({"session_id": "botty-widget", "messages": messages}))

        mock_llm_json = json.dumps({
            "user_memories": [],
            "system_memories": [],
            "skills": [],
            "context_summary": "Working on K380 Bluetooth keyboard SDDM wait service."
        })

        def fake_run(cmd, **kwargs):
            return {"ok": True, "returncode": 0,
                    "stdout": f"```json\n{mock_llm_json}\n```",
                    "stderr": "", "timed_out": False, "output_truncated": False, "error": None}

        with patch("botty_backend.run_bounded_process", side_effect=fake_run):
            res = botty_backend.distill_and_compact_session(force=False, preserve_tail=4)

        self.assertTrue(res["ok"])
        # Bridge file must exist and contain the distilled summary
        self.assertTrue(botty_backend.CONTEXT_BRIDGE_FILE.exists())
        bridge = botty_backend.CONTEXT_BRIDGE_FILE.read_text(encoding="utf-8")
        self.assertIn("K380 Bluetooth keyboard SDDM wait service", bridge)
        # The preserved tail must also be carried over
        self.assertIn("Turn 15", bridge)

    def test_ask_injects_continuity_bridge_into_prompt(self):
        """ask() must prepend the continuity bridge to the model prompt so the
        agent never loses prior context after a Hermes session reset."""
        # Seed a bridge file
        botty_backend.CONTEXT_BRIDGE_FILE.write_text(
            "[COMPACTED CONTEXT] K380 keyboard task in progress.", encoding="utf-8"
        )
        # Avoid actually spawning hermes: mock run_bounded_process to capture the
        # composed command. ask() deletes current_query.txt after the call, so we
        # spy on Path.write_text to capture the prompt handed to the model.
        captured_cmds = []
        written_files = {}

        orig_write = botty_backend.Path.write_text
        def spy_write(self, data, *a, **k):
            written_files[str(self)] = data
            return orig_write(self, data, *a, **k)

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return {"ok": True, "returncode": 0, "stdout": "Done.", "stderr": "",
                    "timed_out": False, "output_truncated": False, "error": None}

        with patch("botty_backend.run_bounded_process", side_effect=fake_run), \
             patch("botty_backend.Path.write_text", spy_write), \
             patch("botty_backend.set_status"), \
             patch("botty_backend.append_botty_log"), \
             patch("botty_backend.add_history_message"), \
             patch("botty_backend.send_system_notification"), \
             patch("botty_backend.update_encrypted_vault"), \
             patch("botty_backend.get_active_engine", return_value="hermes"), \
             patch("botty_backend.get_active_model_for_engine", return_value={"model": "x", "provider": "y"}), \
             patch("botty_backend.get_sandbox_mode", return_value=True):
            botty_backend.ask("can you check if it's done now?")

        hermes_cmds = [c for c in captured_cmds if isinstance(c, (list, tuple)) and "--query-file" in c]
        self.assertTrue(hermes_cmds, "hermes command with --query-file was not spawned")
        # The prompt written to current_query.txt is what the model receives
        query_file_writes = [v for k, v in written_files.items() if "current_query" in k]
        self.assertTrue(query_file_writes, "current_query.txt prompt was never written")
        prompt = query_file_writes[0]
        self.assertIn("CARRIED-OVER CONTEXT FROM PRIOR COMPACTED SESSION", prompt)
        self.assertIn("K380 keyboard task in progress", prompt)
        self.assertIn("can you check if it's done now?", prompt)
        # F1: sandboxed run must NOT carry --yolo (technical enforcement stays on).
        for c in captured_cmds:
            self.assertNotIn("--yolo", c, "sandboxed hermes run must not bypass approvals")

    def test_bypass_run_executes_approved_scope_not_raw_attachment(self):
        """F1: the human-approved re-run must execute the STORED proposal, never
        re-inline raw untrusted attachment/screen content at execution time."""
        # History contains an assistant permission-request message (the proposal).
        self.history_file.write_text(json.dumps({
            "session_id": "botty-widget",
            "messages": [
                {"id": "m1", "role": "user", "content": "user query", "attachments": [], "actions": []},
                {"id": "m2", "role": "assistant", "sandbox_request": True,
                 "content": "🔒 SANDBOX PERMISSION REQUIRED: touch the file\nProposed Actions:\n- touch /tmp/x",
                 "attachments": [], "actions": []}
            ]
        }))
        written_files = {}
        orig_write = botty_backend.Path.write_text
        def spy_write(self, data, *a, **k):
            written_files[str(self)] = data
            return orig_write(self, data, *a, **k)
        captured_cmds = []
        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return {"ok": True, "returncode": 0, "stdout": "Done.", "stderr": "",
                    "timed_out": False, "output_truncated": False, "error": None}

        with patch("botty_backend.run_bounded_process", side_effect=fake_run), \
             patch("botty_backend.Path.write_text", spy_write), \
             patch("botty_backend.set_status"), \
             patch("botty_backend.append_botty_log"), \
             patch("botty_backend.add_history_message"), \
             patch("botty_backend.send_system_notification"), \
             patch("botty_backend.update_encrypted_vault"), \
             patch("botty_backend.get_active_engine", return_value="hermes"), \
             patch("botty_backend.get_active_model_for_engine", return_value={"model": "x", "provider": "y"}), \
             patch("botty_backend.get_sandbox_mode", return_value=True):
            # bypass_sandbox=True simulates the human clicking Approve.
            botty_backend.ask("I approve this action. Please bypass the sandbox and execute the proposed changes.",
                              bypass_sandbox=True)

        query_file_writes = [v for k, v in written_files.items() if "current_query" in k]
        self.assertTrue(query_file_writes, "current_query.txt prompt was never written")
        prompt = query_file_writes[0]
        # Approved payload (proposal) is the execution instruction...
        self.assertIn("APPROVED EXECUTION SCOPE", prompt)
        self.assertIn("SANDBOX PERMISSION REQUIRED: touch the file", prompt)
        # ...and the untrusted "user query" text is NOT re-inlined at execution time.
        self.assertNotIn("ATTACHED FILE", prompt)
        # Bypass run may carry --yolo (single explicit human-approved execution).
        for c in captured_cmds:
            if "--query-file" in c:
                self.assertIn("--yolo", c)

    def test_clear_history_resets_session(self):
        self.history_file.write_text(json.dumps({
            "session_id": "botty-widget",
            "messages": [{"role": "user", "content": "hello"}]
        }))

        res = botty_backend.clear_history()
        self.assertTrue(res["ok"])

        # History should be empty
        h = json.loads(self.history_file.read_text())
        self.assertEqual(h["messages"], [])

        # Hermes session should be renamed/archived
        con = sqlite3.connect(str(self.hermes_state_db))
        cur = con.cursor()
        cur.execute("SELECT title FROM sessions WHERE id = 'sess_123'")
        title = cur.fetchone()[0]
        self.assertTrue(title.startswith("archived-botty-widget-"))
        con.close()

    def test_bounded_process_caps_runaway_output(self):
        """F4: a process that floods stdout must be killed at the cap, not
        buffered unboundedly into RAM."""
        code = "import sys\nwhile True: sys.stdout.write('x' * 65536)\n"
        run = botty_backend.run_bounded_process(
            [sys.executable, "-c", code],
            timeout_s=10,
            max_output_chars=100_000,
        )
        self.assertTrue(run["ok"])
        self.assertTrue(run["output_truncated"], "runaway output must be capped")
        self.assertLessEqual(len(run["stdout"]), 100_000 + 65536)
        self.assertIn(run["returncode"], (-9, -15), "runaway must be killed")

    def test_bounded_process_kills_whole_group_on_timeout(self):
        """F4: timeout must kill the process GROUP — a child spawned by the
        direct process must not survive."""
        # Spawn a helper python that writes its PID then sleeps forever; the
        # parent (this process's child) also sleeps. On timeout the whole group
        # must be gone.
        marker = self.data_dir / "child_alive.txt"
        helper = Path(__file__).parent / "test_helper_sleeper.py"

        # Child process: spawn the sleeper helper and wait.
        parent_code = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, %r, %r])\n"
            "time.sleep(60)\n"
        ) % (str(helper), str(marker))
        run = botty_backend.run_bounded_process(
            [sys.executable, "-c", parent_code],
            timeout_s=2,
            max_output_chars=10_000,
        )
        self.assertTrue(run["ok"])
        self.assertTrue(run["timed_out"], "must report timeout")
        # The child wrote its PID; after group kill it must no longer exist.
        self.assertTrue(marker.exists(), "child never started")
        child_pid = int(marker.read_text().strip())
        # Give the kernel a moment to reap, then assert the child is gone.
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail(f"child process {child_pid} survived the group kill")

    def test_proposal_schema_rejects_oversized_and_malformed(self):
        """F2: staged proposals must respect the bounded schema."""
        d = {
            "user_memories": ["ok memory", "x" * 5000, "", "ok memory"],
            "system_memories": [],
            "skills": [
                {"name": "bad name!", "description": "d", "instructions": "i"},
                {"name": "good-skill", "description": "d", "instructions": "i"},
            ],
            "context_summary": "s",
        }
        staged = botty_backend.stage_learning_proposals(d)
        # 1 user memory survives dedupe+cap; 1 skill survives name validation.
        self.assertEqual(staged["user_memories"], 1)
        self.assertEqual(staged["skills"], 1)
        props = botty_backend.get_proposals()
        texts = [p.get("text") for p in props["proposals"] if p["kind"] == "memory"]
        names = [p.get("name") for p in props["proposals"] if p["kind"] == "skill"]
        self.assertEqual(texts, ["ok memory"])
        self.assertEqual(names, ["good-skill"])

if __name__ == "__main__":
    unittest.main()

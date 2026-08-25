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

        botty_backend.BOTTY_DATA_DIR = self.data_dir
        botty_backend.HISTORY_FILE = self.history_file
        botty_backend.HISTORY_ARCHIVE_FILE = self.history_archive_file
        botty_backend.CONFIG_FILE = self.config_file
        botty_backend.STATUS_FILE = self.status_file
        botty_backend.LOCK_FILE = self.data_dir / "running.pid"
        botty_backend.HERMES_MEMORY_DIR = self.hermes_mem_dir
        botty_backend.HERMES_SKILLS_DIR = self.hermes_skills_dir
        botty_backend.HERMES_STATE_DB = self.hermes_state_db

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
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = botty_backend.subprocess.TimeoutExpired(["omp"], 901)

        with patch("botty_backend.set_status"), \
             patch("botty_backend.append_botty_log"), \
             patch("botty_backend.add_history_message"), \
             patch("botty_backend.send_system_notification"), \
             patch("botty_backend.get_active_engine", return_value="omp"), \
             patch("botty_backend.get_active_model_for_engine", return_value={"model": "test", "provider": "test"}), \
             patch("botty_backend.get_sandbox_mode", return_value=False), \
             patch("botty_backend.subprocess.Popen", return_value=mock_proc):
            result = botty_backend.ask("hello")

        mock_proc.communicate.assert_called_once_with(timeout=901)
        self.assertEqual(result, {"ok": False, "error": "Request timed out after 901 seconds."})

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
                    "instructions": "Run `systemctl --user restart pipewire pipewire-pulse`."
                }
            ],
            "context_summary": "Discussed Omarchy audio and window management configurations."
        })

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = f"Here is the distillation:\n```json\n{mock_llm_json}\n```"

        with patch("subprocess.run", return_value=mock_proc):
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

        # Verify memories were added
        mems = botty_backend.get_memories()
        self.assertEqual(mems["count"], 2)
        mem_texts = [m["text"] for m in mems["memories"]]
        self.assertTrue(any("Python 3.11" in t for t in mem_texts))
        self.assertTrue(any("Hyprland socket" in t for t in mem_texts))

        # Verify skill was created
        skills = botty_backend.get_skills()
        self.assertEqual(skills["count"], 1)
        self.assertEqual(skills["skills"][0]["name"], "audio-restart")

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
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = f"```json\n{mock_llm_json}\n```"
        with patch("subprocess.run", return_value=mock_proc):
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
        # Avoid actually spawning hermes/openssl: mock Popen to capture the composed prompt.
        # Collect ALL Popen invocations; ask() also spawns openssl for the vault update.
        # ask() deletes current_query.txt after the call, so we spy on Path.write_text to
        # capture the prompt that was actually handed to the model.
        captured_cmds = []
        written_files = {}

        orig_write = botty_backend.Path.write_text
        def spy_write(self, data, *a, **k):
            written_files[str(self)] = data
            return orig_write(self, data, *a, **k)

        class FakeProc:
            def __init__(self, *a, **k):
                captured_cmds.append(a[0] if a else k.get("args"))
            def communicate(self, timeout=None):
                return ("Done.", "")
            @property
            def returncode(self):
                return 0

        with patch("botty_backend.subprocess.Popen", FakeProc), \
             patch("botty_backend.Path.write_text", spy_write), \
             patch("botty_backend.set_status"), \
             patch("botty_backend.append_botty_log"), \
             patch("botty_backend.add_history_message"), \
             patch("botty_backend.send_system_notification"), \
             patch("botty_backend.get_active_engine", return_value="hermes"), \
             patch("botty_backend.get_active_model_for_engine", return_value={"model": "x", "provider": "y"}):
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

if __name__ == "__main__":
    unittest.main()

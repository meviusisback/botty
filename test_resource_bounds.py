"""Regression tests for the HANCORE 1.0.10 resource-control review gates.

A clipboard offer is attacker-influencable and a retained JSONL archive grows
for the life of the profile. These tests prove the new ceilings actually bound
reads and writes, and that each guard fails when removed.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import botty_backend

# Safety net: no test may write into the live profile, even on import.
_MODULE_TMP = tempfile.TemporaryDirectory(prefix="botty-bounds-tests-")
_MODULE_DATA = Path(_MODULE_TMP.name) / "data"
_MODULE_DATA.mkdir(parents=True, exist_ok=True)
for _name, _rel in (
    ("BOTTY_DATA_DIR", None),
    ("HISTORY_FILE", "history.json"),
    ("HISTORY_ARCHIVE_FILE", "history_archive.jsonl"),
    ("STATUS_FILE", "status.json"),
    ("SCREENSHOT_DIR", "captures"),
    ("BOTTY_LOG_FILE", "botty.log"),
    ("HERMES_BOTTY_DIR", "hermes_profile"),
    ("HERMES_BOTTY_STATE_DB", "hermes_profile/state.db"),
):
    _target = _MODULE_DATA if _rel is None else _MODULE_DATA / _rel
    if _rel in ("captures",):
        _target.mkdir(parents=True, exist_ok=True)
    if hasattr(botty_backend, _name):
        setattr(botty_backend, _name, _target)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class TestBoundedBinaryCapture(unittest.TestCase):
    def test_small_output_is_returned_verbatim(self):
        res = botty_backend._capture_bounded_binary(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'hello')"],
            1024, 2.0)
        self.assertEqual(res["data"], b"hello")
        self.assertFalse(res["overflow"])
        self.assertFalse(res["timed_out"])

    def test_oversized_output_is_capped_and_flagged(self):
        # A child that emits far more than the cap: the helper must stop at the
        # cap (plus a single sentinel byte) instead of buffering the whole blob.
        res = botty_backend._capture_bounded_binary(
            [sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(b'x'*300000)"],
            1000, 3.0)
        self.assertTrue(res["overflow"], "cap must be reported, not silently ignored")
        self.assertEqual(len(res["data"]), 1000)

    def test_streaming_child_hits_the_deadline(self):
        res = botty_backend._capture_bounded_binary(
            [sys.executable, "-c",
             "import sys,time\n"
             "while True:\n"
             "    sys.stdout.buffer.write(b'.')\n"
             "    sys.stdout.buffer.flush()\n"
             "    time.sleep(0.02)\n"],
            100000, 0.3)
        self.assertTrue(res["timed_out"])
        self.assertLess(len(res["data"]), 100000)


class TestClipboardImageCeiling(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.test_dir.cleanup)
        self.shots = Path(self.test_dir.name) / "captures"
        self.shots.mkdir()
        self._shots = botty_backend.SCREENSHOT_DIR
        botty_backend.SCREENSHOT_DIR = self.shots

    def tearDown(self):
        botty_backend.SCREENSHOT_DIR = self._shots

    def test_oversized_offer_is_refused_without_writing_a_file(self):
        cap = {"data": b"\x89PNG" + b"x" * 10, "overflow": True,
               "timed_out": False, "returncode": -9, "error": None}
        with mock.patch.object(botty_backend, "_capture_bounded_binary", return_value=cap):
            res = botty_backend.get_clipboard_image()
        self.assertFalse(res["ok"])
        self.assertEqual(list(self.shots.glob("clip_*.png")), [])

    def test_valid_small_png_is_written_with_private_permissions(self):
        cap = {"data": PNG, "overflow": False, "timed_out": False,
               "returncode": 0, "error": None}
        with mock.patch.object(botty_backend, "_capture_bounded_binary", return_value=cap):
            res = botty_backend.get_clipboard_image()
        self.assertTrue(res["ok"], res)
        path = Path(res["image_path"])
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TestSelectionRead(unittest.TestCase):
    def test_runner_gets_the_source_cap_and_timeout(self):
        with mock.patch.object(botty_backend, "run_bounded_process", return_value={
                "stdout": "hello", "output_truncated": False,
                "timed_out": False}) as runner:
            with mock.patch.object(botty_backend, "redact_secrets", side_effect=lambda s: s):
                self.assertEqual(botty_backend._read_primary_selection(), "hello")
        runner.assert_called_once()
        args, kwargs = runner.call_args
        self.assertEqual(args[0], ["wl-paste", "--primary"])
        self.assertEqual(kwargs["max_output_chars"], botty_backend.MAX_SELECTION_CHARS)
        self.assertEqual(kwargs["timeout_s"], 0.3)

    def test_oversized_selection_still_fits_the_ceiling(self):
        cap = botty_backend.MAX_SELECTION_CHARS
        with mock.patch.object(botty_backend, "run_bounded_process", return_value={
                "stdout": "A" * cap, "output_truncated": True, "timed_out": False}):
            with mock.patch.object(botty_backend, "redact_secrets", side_effect=lambda s: s):
                out = botty_backend._read_primary_selection()
        self.assertLessEqual(len(out), cap)
        self.assertTrue(out.endswith("… [truncated]"))


class TestQMLProcessGuards(unittest.TestCase):
    """Every panel child process needs both a runtime and an output ceiling.

    StdioCollector.text is readonly and buffers everything with waitForEnd:true,
    so the collector must stream incrementally and kill its owner past the cap.
    """
    PANEL = Path(__file__).with_name("Panel.qml")

    def test_every_process_has_a_watchdog_registration(self):
        text = self.PANEL.read_text(encoding="utf-8")
        processes = text.count("\n  Process {")
        guarded = text.count("root.procWatchStart(this)")
        self.assertGreater(processes, 0)
        self.assertEqual(guarded, processes,
                         "each Process must register with the watchdog")

    def test_every_collector_is_bounded(self):
        text = self.PANEL.read_text(encoding="utf-8")
        collectors = text.count("StdioCollector {")
        bounded = text.count("root.procOutputOverflow(")
        self.assertGreater(collectors, 0)
        self.assertEqual(bounded, collectors,
                         "each StdioCollector must have an output ceiling")
        self.assertNotIn("waitForEnd: true", text)


class TestArchiveCeiling(unittest.TestCase):
    def test_only_newest_lines_survive(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "history_archive.jsonl"
            archive.write_text("".join('{"n":%d}\n' % n for n in range(50)), encoding="utf-8")
            botty_backend._trim_archive(archive, max_lines=5)
            kept = archive.read_text(encoding="utf-8").splitlines()
            self.assertEqual(kept, ['{"n":%d}' % n for n in range(45, 50)])


if __name__ == "__main__":
    unittest.main()

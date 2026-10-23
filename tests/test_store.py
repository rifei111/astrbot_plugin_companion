"""状态落盘的测试，重点是"坏文件不能把插件搞挂"。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.models import SessionState
from engine.store import MAX_DECISIONS, StateStore


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = StateStore(self.dir / "data")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        state = SessionState(umo="umo:1", chat_type="private", enabled=True)
        state.touch(1000.0, "user")
        state.add_recent("user", "你好", 1000.0)
        state.unanswered = 1
        sessions = {"umo:1": state}
        decisions = [{"ts": 1.0, "umo": "umo:1", "reason": "care"}]
        self.store.save(sessions, decisions)

        loaded_sessions, loaded_decisions = self.store.load()
        self.assertIn("umo:1", loaded_sessions)
        self.assertTrue(loaded_sessions["umo:1"].enabled)
        self.assertEqual(loaded_sessions["umo:1"].last_user_ts, 1000.0)
        self.assertEqual(loaded_sessions["umo:1"].recent[0].text, "你好")
        self.assertEqual(len(loaded_decisions), 1)

    def test_missing_file_returns_empty(self):
        sessions, decisions = self.store.load()
        self.assertEqual(sessions, {})
        self.assertEqual(decisions, [])

    def test_corrupt_file_is_backed_up(self):
        self.store.data_dir.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json", encoding="utf-8")
        sessions, decisions = self.store.load()
        self.assertEqual(sessions, {})
        self.assertTrue(list(self.store.data_dir.glob("state.corrupt-*.json")))

    def test_decisions_are_capped_on_save(self):
        decisions = [{"i": i} for i in range(MAX_DECISIONS + 50)]
        self.store.save({}, decisions)
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["decisions"]), MAX_DECISIONS)

    def test_sessions_without_umo_are_skipped(self):
        self.store.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"sessions": {"bad": {"no_umo": True}, "ok": {"umo": "x"}}}
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        sessions, _ = self.store.load()
        self.assertIn("x", sessions)
        self.assertEqual(len(sessions), 1)


if __name__ == "__main__":
    unittest.main()

"""会话状态里跟"回应率"有关的行为。

回应率是判断主动消息有没有白发的核心指标，一边倒的 bug 会让它超过 100%，
所以这里单独盯住 note_user_reply 的去重语义。
"""

import unittest

from engine.models import SessionState

WINDOW = 30 * 60


def make_state(proactive_ts=1000.0):
    state = SessionState(umo="test:private:1", enabled=True)
    state.last_proactive_ts = proactive_ts
    state.sent_total = 1
    return state


class TestNoteUserReply(unittest.TestCase):
    def test_counts_first_reply(self):
        state = make_state()
        self.assertTrue(state.note_user_reply(1000.0 + 60, WINDOW))
        self.assertEqual(state.replied_total, 1)

    def test_counts_only_once_per_proactive_message(self):
        """用户连回三条，只算一条被回应过的主动消息。"""
        state = make_state()
        for offset in (60, 90, 120):
            state.note_user_reply(1000.0 + offset, WINDOW)
        self.assertEqual(state.replied_total, 1)
        self.assertLessEqual(state.replied_total, state.sent_total)

    def test_new_proactive_message_can_be_counted_again(self):
        state = make_state()
        state.note_user_reply(1060.0, WINDOW)
        state.last_proactive_ts = 2000.0
        state.last_proactive_replied = False
        state.sent_total = 2
        self.assertTrue(state.note_user_reply(2060.0, WINDOW))
        self.assertEqual(state.replied_total, 2)

    def test_outside_window_not_counted(self):
        state = make_state()
        self.assertFalse(state.note_user_reply(1000.0 + WINDOW + 1, WINDOW))
        self.assertEqual(state.replied_total, 0)
        self.assertFalse(state.last_proactive_replied)

    def test_no_proactive_message(self):
        state = SessionState(umo="test:private:1")
        self.assertFalse(state.note_user_reply(1000.0, WINDOW))
        self.assertEqual(state.replied_total, 0)

    def test_flag_survives_round_trip(self):
        state = make_state()
        state.note_user_reply(1060.0, WINDOW)
        restored = SessionState.from_dict(state.to_dict())
        self.assertTrue(restored.last_proactive_replied)
        self.assertEqual(restored.replied_total, 1)

    def test_old_state_without_flag_still_loads(self):
        raw = {
            "umo": "test:private:1",
            "last_proactive_ts": 1000.0,
            "sent_total": 1,
            "replied_total": 1,
        }
        restored = SessionState.from_dict(raw)
        self.assertFalse(restored.last_proactive_replied)


if __name__ == "__main__":
    unittest.main()

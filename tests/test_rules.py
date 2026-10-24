"""规则判定的单元测试。

重点覆盖"什么时候绝对不能开口"，因为主动消息的事故都是漏掉一条闸门造成的。
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime

from engine import rules
from engine.models import SessionState


def make_session(**overrides) -> SessionState:
    state = SessionState(umo="test:private:1", chat_type="private", enabled=True)
    state.last_user_ts = 1000.0
    state.last_bot_ts = 1000.0
    state.last_activity_ts = 1000.0
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


class TestQuietHours(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(rules.parse_hhmm("23:30", (0, 0)), 23 * 60 + 30)
        self.assertEqual(rules.parse_hhmm("08:00", (0, 0)), 8 * 60)
        self.assertEqual(rules.parse_hhmm("bad", (7, 7)), 7 * 60 + 7)

    def test_cross_midnight(self):
        self.assertTrue(rules.in_quiet_hours(23 * 60 + 45, "23:30", "08:00"))
        self.assertTrue(rules.in_quiet_hours(2 * 60, "23:30", "08:00"))
        self.assertFalse(rules.in_quiet_hours(12 * 60, "23:30", "08:00"))
        self.assertFalse(rules.in_quiet_hours(8 * 60, "23:30", "08:00"))

    def test_same_start_end_means_no_quiet(self):
        self.assertFalse(rules.in_quiet_hours(3 * 60, "00:00", "00:00"))


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.cfg = rules.RuleConfig()
        self.now = 1000.0 + 600  # 安静了 10 分钟
        self.local = datetime(2026, 10, 23, 21, 0)
        self.rng = random.Random(7)

    def test_disabled(self):
        state = make_session(enabled=False)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertFalse(decision.act)
        self.assertEqual(decision.reason, "disabled")

    def test_no_activity(self):
        state = SessionState(umo="u", enabled=True)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "no_activity")

    def test_group_blocked_by_default(self):
        state = make_session(chat_type="group")
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "group_not_allowed")

    def test_quiet_hours(self):
        state = make_session()
        local = datetime(2026, 10, 23, 23, 45)
        decision = rules.evaluate(self.now, local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "quiet_hours")

    def test_waiting_when_too_soon(self):
        state = make_session()
        decision = rules.evaluate(1000.0 + 60, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "waiting")
        self.assertLess(decision.explain["idle_s"], decision.explain["need_s"])

    def test_daily_limit(self):
        today = self.local.strftime("%Y-%m-%d")
        state = make_session(count_date=today, count_today=self.cfg.max_per_day)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "daily_limit")

    def test_daily_limit_resets_next_day(self):
        state = make_session(count_date="2020-01-01", count_today=99)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertTrue(decision.act)

    def test_min_interval(self):
        state = make_session(last_proactive_ts=self.now - 60)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "min_interval")

    def test_unanswered_cooldown(self):
        state = make_session(unanswered=self.cfg.unanswered_stop)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "unanswered_cooldown")

    def test_backoff_raises_threshold(self):
        state = make_session(unanswered=1)
        # 安静 5 分钟：正常情况下够了，但被无视一次后门槛翻倍到 8 分钟。
        decision = rules.evaluate(
            1000.0 + 300,
            self.local,
            state,
            rules.RuleConfig(unanswered_stop=3),
            self.rng,
        )
        self.assertFalse(decision.act)
        self.assertEqual(decision.reason, "waiting")
        self.assertGreaterEqual(decision.explain["need_s"], 240 * 2)

    def test_stale_window(self):
        state = make_session()
        much_later = 1000.0 + 7 * 3600
        decision = rules.evaluate(much_later, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "stale")

    def test_bot_hasnt_replied(self):
        state = make_session(last_user_ts=1000.0 + 300, last_bot_ts=1000.0)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "bot_hasnt_replied")

    def test_act_and_reason(self):
        state = make_session()
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertTrue(decision.act)
        self.assertIn(decision.reason, {"care", "followup", "impulse", "night"})
        self.assertGreater(decision.explain["idle_s"], 0)

    def test_night_reason_before_quiet_hours(self):
        """深夜窗口现在从静默开始时间倒推：默认是 22:30–23:30。"""
        state = make_session()
        local = datetime(2026, 10, 23, 23, 0)
        decision = rules.evaluate(self.now, local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "night")

    def test_night_window_follows_quiet_start(self):
        """静默开始改到 22:00，深夜窗口就变成 21:00–22:00，不会留在旧位置。"""
        state = make_session()
        cfg = rules.RuleConfig(quiet_start="22:00", quiet_end="07:00")
        local = datetime(2026, 10, 23, 21, 30)
        self.assertEqual(rules.choose_reason(state, local, cfg, self.rng), "night")
        local_late = datetime(2026, 10, 23, 23, 0)
        self.assertNotEqual(
            rules.choose_reason(state, local_late, cfg, self.rng), "night"
        )

    def test_night_reason_never_inside_quiet_hours(self):
        """静默时段里直接不判定，深夜理由不会和静默打架。"""
        state = make_session()
        local = datetime(2026, 10, 23, 1, 0)
        decision = rules.evaluate(self.now, local, state, self.cfg, self.rng)
        self.assertFalse(decision.act)
        self.assertEqual(decision.reason, "quiet_hours")

    def test_followup_reason_on_question(self):
        state = make_session()
        state.add_recent("user", "你觉得这个怎么办？", 1000.0)
        decision = rules.evaluate(self.now, self.local, state, self.cfg, self.rng)
        self.assertEqual(decision.reason, "followup")

    def test_impulse_disabled_when_probability_zero(self):
        state = make_session()
        cfg = rules.RuleConfig(impulse_probability=0.0)
        for seed in range(20):
            decision = rules.evaluate(
                self.now, self.local, state, cfg, random.Random(seed)
            )
            self.assertNotEqual(decision.reason, "impulse")


if __name__ == "__main__":
    unittest.main()

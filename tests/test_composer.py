"""内容生成与清洗的测试。

清洗这一层看着琐碎，但它是"AI 味"进入聊天窗口的最后一道闸：模型经常带
"我："、带列表、一次写三行，都会在这里被削掉。
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime
from types import SimpleNamespace

from engine import composer
from engine.models import SessionState
from tests import FixedRng


def make_session() -> SessionState:
    state = SessionState(umo="umo:1", enabled=True)
    state.touch(1000.0, "user")
    state.add_recent("user", "今天好累", 1000.0)
    state.add_recent("bot", "怎么了", 1010.0)
    return state


class TestSanitize(unittest.TestCase):
    def test_strip_role_prefix(self):
        self.assertEqual(composer.sanitize("我：在忙吗？"), "在忙吗？")
        self.assertEqual(composer.sanitize("AI: 在忙吗"), "在忙吗")

    def test_strip_wrapping_quotes(self):
        self.assertEqual(composer.sanitize("“在忙吗？”"), "在忙吗？")

    def test_drop_list_markers(self):
        self.assertEqual(composer.sanitize("- 在忙吗\n- 吃饭了吗"), "在忙吗 吃饭了吗")

    def test_collapse_blank_lines(self):
        self.assertEqual(composer.sanitize("在忙吗\n\n\n吃饭了吗"), "在忙吗 吃饭了吗")

    def test_truncate_long_text(self):
        text = "今天" * 200
        out = composer.sanitize(text, max_chars=40)
        self.assertLessEqual(len(out), 40)

    def test_empty(self):
        self.assertEqual(composer.sanitize(""), "")
        self.assertEqual(composer.sanitize(None), "")


class TestSplitBubbles(unittest.TestCase):
    def test_short_text_not_split(self):
        self.assertEqual(
            composer.split_bubbles("在忙吗？", max_bubbles=3), ["在忙吗？"]
        )

    def test_long_text_can_split(self):
        text = "今天加班到好晚。感觉整个人都被掏空了。明天还得早起。"
        bubbles = composer.split_bubbles(text, max_bubbles=3, rng=FixedRng())
        self.assertGreaterEqual(len(bubbles), 2)
        self.assertLessEqual(len(bubbles), 3)
        self.assertEqual("".join(bubbles), text)

    def test_max_bubbles_one(self):
        text = "今天加班到好晚。感觉整个人都被掏空了。"
        self.assertEqual(composer.split_bubbles(text, max_bubbles=1), [text])

    def test_empty(self):
        self.assertEqual(composer.split_bubbles(""), [])


class TestPickTemplate(unittest.TestCase):
    def test_uses_reason_pool(self):
        rng = random.Random(3)
        text = composer.pick_template(
            "night", {"night": ["还没睡啊"], "fallback": ["x"]}, rng
        )
        self.assertEqual(text, "还没睡啊")

    def test_unknown_reason_falls_back(self):
        text = composer.pick_template("nope", {"fallback": ["兜底"]}, random.Random(0))
        self.assertEqual(text, "兜底")

    def test_multiline_string_templates(self):
        """配置面板里模板是"一行一句"的文本框，要能拆成列表。"""
        templates = {"care": "在吗\n忙不忙\n", "fallback": ""}
        picks = {
            composer.pick_template("care", templates, random.Random(seed))
            for seed in range(20)
        }
        self.assertEqual(picks, {"在吗", "忙不忙"})

    def test_empty_templates_use_builtin(self):
        text = composer.pick_template("care", {"care": ""}, random.Random(0))
        self.assertIn(text, composer.DEFAULT_TEMPLATES["care"])


class TestBuildPrompt(unittest.TestCase):
    def test_contains_context_and_recent(self):
        prompt = composer.build_prompt(
            "care", make_session(), datetime(2026, 10, 23, 21, 0), now_ts=1020.0
        )
        self.assertIn("<context>", prompt)
        self.assertIn("2026-10-23 21:00", prompt)
        self.assertIn("今天好累", prompt)
        self.assertIn("直接给出你要发的那句话", prompt)


class TestComposeLLM(unittest.IsolatedAsyncioTestCase):
    async def test_cleans_provider_output(self):
        class FakeProvider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(completion_text="我：在忙吗？\n- 记得吃饭")

        text = await composer.compose_llm(
            FakeProvider(), "care", make_session(), datetime(2026, 10, 23, 21, 0)
        )
        self.assertEqual(text, "在忙吗？ 记得吃饭")

    async def test_provider_error_returns_empty(self):
        class BrokenProvider:
            async def text_chat(self, **kwargs):
                raise RuntimeError("boom")

        text = await composer.compose_llm(
            BrokenProvider(), "care", make_session(), datetime(2026, 10, 23, 21, 0)
        )
        self.assertEqual(text, "")

    async def test_none_provider(self):
        text = await composer.compose_llm(
            None, "care", make_session(), datetime(2026, 10, 23, 21, 0)
        )
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()

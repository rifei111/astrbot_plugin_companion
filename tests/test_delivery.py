"""发送节奏的测试：延迟必须落在范围内、分条必须真的分批发出。"""

from __future__ import annotations

import random
import unittest

from engine import delivery
from tests import FixedRng


class TestSampleDelay(unittest.TestCase):
    def setUp(self):
        self.cfg = delivery.DeliveryConfig()

    def test_in_range(self):
        rng = random.Random(0)
        for _ in range(200):
            value = delivery.sample_delay_ms(self.cfg, rng)
            self.assertGreaterEqual(value, self.cfg.delay_min_ms)
            self.assertLessEqual(value, self.cfg.delay_max_ms)

    def test_zero_sigma_is_median(self):
        cfg = delivery.DeliveryConfig(delay_sigma=0.0, delay_median_ms=2600)
        self.assertEqual(delivery.sample_delay_ms(cfg, random.Random(0)), 2600)

    def test_variance_exists(self):
        rng = random.Random(5)
        values = {delivery.sample_delay_ms(self.cfg, rng) for _ in range(50)}
        self.assertGreater(len(values), 1)

    def test_clamped_to_max(self):
        cfg = delivery.DeliveryConfig(delay_median_ms=100000, delay_max_ms=3000)
        self.assertEqual(delivery.sample_delay_ms(cfg, random.Random(0)), 3000)


class TestDeliver(unittest.IsolatedAsyncioTestCase):
    async def test_sends_all_bubbles(self):
        sent: list[tuple[str, str]] = []
        slept: list[float] = []

        async def fake_send(umo: str, text: str) -> bool:
            sent.append((umo, text))
            return True

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        cfg = delivery.DeliveryConfig(bubbles_max=3, delay_sigma=0.0)
        result = await delivery.deliver(
            "umo:1",
            "今天加班到好晚。感觉整个人都被掏空了。",
            fake_send,
            cfg,
            FixedRng(),
            sleep=fake_sleep,
        )
        self.assertEqual(len(result), len(sent))
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(len(slept), 2)  # 首条延迟 + 条间停顿
        self.assertGreater(slept[0], 0)

    async def test_stops_on_failure(self):
        sent: list[str] = []

        async def failing_send(umo: str, text: str) -> bool:
            sent.append(text)
            return False

        async def noop_sleep(seconds: float) -> None:
            return None

        cfg = delivery.DeliveryConfig(bubbles_max=3, delay_sigma=0.0)
        result = await delivery.deliver(
            "umo:1",
            "今天加班到好晚。感觉整个人都被掏空了。明天还得早起。",
            failing_send,
            cfg,
            FixedRng(),
            sleep=noop_sleep,
        )
        self.assertEqual(result, [])
        self.assertEqual(len(sent), 1)  # 失败后不再补发

    async def test_empty_text(self):
        async def fake_send(umo: str, text: str) -> bool:
            raise AssertionError("不该发空消息")

        async def noop_sleep(seconds: float) -> None:
            return None

        result = await delivery.deliver(
            "umo:1", "   ", fake_send, delivery.DeliveryConfig(), sleep=noop_sleep
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()

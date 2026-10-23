"""发送节奏：延迟采样 + 分条 + 条间停顿。

这里只管"怎么发"，不管"该不该发"（那是 rules 的事），也不决定"发什么"
（那是 composer 的事）。之所以把延迟单独拆出来，是因为它是最容易被写成固定
值的地方——一旦固定，整件事就假了。
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Awaitable, Callable

from .composer import split_bubbles


@dataclass
class DeliveryConfig:
    """延迟与分条的参数。默认值对应"手机上随手回"的速度。"""

    delay_median_ms: int = 2600
    delay_sigma: float = 0.55
    delay_max_ms: int = 15000
    delay_min_ms: int = 800
    bubbles_max: int = 3

    @classmethod
    def from_config(cls, config: dict | None) -> "DeliveryConfig":
        cfg = config or {}

        def _get(key, default):
            value = cfg.get(key, default)
            return default if value is None else value

        return cls(
            delay_median_ms=int(_get("delay_median_ms", 2600)),
            delay_sigma=float(_get("delay_sigma", 0.55)),
            delay_max_ms=int(_get("delay_max_ms", 15000)),
            delay_min_ms=800,
            bubbles_max=max(1, int(_get("bubbles_max", 3))),
        )


def sample_delay_ms(cfg: DeliveryConfig, rng: random.Random | None = None) -> int:
    """从对数正态分布里采一个延迟。

    用对数正态而不是均匀分布，是因为"回复时间"天然是重尾的：大多数是几秒，
    偶尔会拖很久。固定值或者均匀分布都会让节奏变得可预测，而可预测=机器。
    """
    rng = rng or random.Random()
    median = max(1, cfg.delay_median_ms)
    sigma = max(0.0, cfg.delay_sigma)
    z = rng.gauss(0.0, 1.0)
    value = median * math.exp(sigma * z)
    value = min(max(value, cfg.delay_min_ms), max(cfg.delay_min_ms, cfg.delay_max_ms))
    return int(value)


async def deliver(
    umo: str,
    text: str,
    send: Callable[[str, str], Awaitable[bool]],
    cfg: DeliveryConfig,
    rng: random.Random | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[str]:
    """按拟人节奏把一句话发出去，返回实际发成功的分条内容。

    send(umo, text) -> bool，返回 False 表示发送失败，此时立刻停手，不再补发
    后面的分条——半截消息比不发更糟。
    """
    rng = rng or random.Random()
    text = (text or "").strip()
    if not text:
        return []

    bubbles = split_bubbles(text, max_bubbles=cfg.bubbles_max, rng=rng)
    if not bubbles:
        return []

    await sleep(sample_delay_ms(cfg, rng) / 1000.0)
    sent: list[str] = []
    for index, bubble in enumerate(bubbles):
        if index > 0:
            # 条间停顿：真人打完一句会想一下再发下一句。
            await sleep(rng.uniform(0.8, 1.8))
        ok = await send(umo, bubble)
        if not ok:
            break
        sent.append(bubble)
    return sent

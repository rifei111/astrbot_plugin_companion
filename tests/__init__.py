"""测试包。这里放一个固定的伪随机源，让"拆不拆条"这类分支可复现。"""


class FixedRng:
    """把 random()/uniform() 固定成常量，用来稳定触发某个分支。"""

    def __init__(self, value: float = 0.99) -> None:
        self._value = value

    def random(self) -> float:
        return self._value

    def uniform(self, low: float, high: float) -> float:
        return high

    def gauss(self, mu: float, sigma: float) -> float:  # pragma: no cover - 备用
        return mu

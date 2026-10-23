"""拟人主动消息插件的核心引擎。

这里刻意不导入 AstrBot 的任何模块：rules / composer / store / models 都是纯
Python，可以用 unittest 直接跑，方便把"什么时候该开口"这件事单独测干净。
"""

__all__ = ["composer", "delivery", "models", "rules", "store"]

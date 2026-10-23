"""主动时机的纯规则判定（P0 版）。

这一层刻意不做任何模型，只做"人味规则"：多久没说话算久、什么时候绝对不能
开口、一天最多几次、被无视之后怎么退避。规则的好处是可解释、可调试、出错
时一眼能看出是哪条闸门拦的——主动消息最难的部分不是"能不能预测"，是"敢不敢
相信预测"，所以第一版先把规则写死，跑一段时间有数据了再谈模型。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from .models import SessionState


def parse_hhmm(value: str, default: tuple[int, int]) -> int:
    """把 "23:30" 解析成当天的第几分钟；非法输入返回默认值。"""
    try:
        raw = str(value).strip()
        hh, mm = raw.split(":")
        hour, minute = int(hh), int(mm)
    except (ValueError, AttributeError):
        hour, minute = default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = default
    return hour * 60 + minute


def in_quiet_hours(
    minute_of_day: int,
    start: str = "23:30",
    end: str = "08:00",
) -> bool:
    """判断某个时刻是否落在静默时段里，支持跨午夜。

    跨午夜写法（start > end）是常见配置，例如 23:30 到次日 08:00。
    start == end 视为没有静默时段。
    """
    s = parse_hhmm(start, (23, 30))
    e = parse_hhmm(end, (8, 0))
    if s == e:
        return False
    if s < e:
        return s <= minute_of_day < e
    return minute_of_day >= s or minute_of_day < e


def local_day_key(local_dt: datetime) -> str:
    return local_dt.strftime("%Y-%m-%d")


@dataclass
class RuleConfig:
    """从插件配置里摘出来的规则参数，方便测试直接构造。"""

    idle_min_seconds: int = 240
    active_window_hours: float = 6.0
    quiet_start: str = "23:30"
    quiet_end: str = "08:00"
    max_per_day: int = 2
    min_interval_minutes: int = 90
    unanswered_stop: int = 2
    backoff_factor: float = 2.0
    allow_group: bool = False
    impulse_probability: float = 0.15

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "RuleConfig":
        cfg = config or {}

        def _get(key, default):
            value = cfg.get(key, default)
            return default if value is None else value

        return cls(
            idle_min_seconds=int(_get("idle_min_seconds", 240)),
            active_window_hours=float(_get("active_window_hours", 6.0)),
            quiet_start=str(_get("quiet_start", "23:30")),
            quiet_end=str(_get("quiet_end", "08:00")),
            max_per_day=int(_get("max_per_day", 2)),
            min_interval_minutes=int(_get("min_interval_minutes", 90)),
            unanswered_stop=int(_get("unanswered_stop", 2)),
            backoff_factor=float(_get("backoff_factor", 2.0)),
            allow_group=bool(_get("allow_group", False)),
            impulse_probability=float(_get("impulse_probability", 0.15)),
        )


@dataclass
class Decision:
    """一次判定的结果。reason 是闸门名或开口理由，explain 给人看。"""

    act: bool
    reason: str
    explain: dict[str, Any] = field(default_factory=dict)


REASON_DESC = {
    "care": "有一会儿没说话了，你想随便说一句",
    "followup": "上一轮话题还没说完，你想接着问一句",
    "impulse": "没什么理由，就是突然想找他说话",
    "night": "已经很晚了，你还没睡，想问问他睡了没",
}


def choose_reason(
    session: "SessionState",
    local_dt: datetime,
    cfg: RuleConfig,
    rng: random.Random | None = None,
) -> str:
    """挑一个开口理由。顺序有讲究：深夜 > 接话 > 冲动 > 普通关心。"""
    rng = rng or random.Random()
    if local_dt.hour >= 23 or local_dt.hour < 2:
        return "night"

    last_user = next((m for m in reversed(session.recent) if m.role == "user"), None)
    if last_user is not None:
        text = last_user.text.strip()
        if text.endswith(("?", "？")) or len(text) >= 30:
            return "followup"

    if rng.random() < cfg.impulse_probability:
        return "impulse"
    return "care"


def evaluate(
    now_ts: float,
    local_dt: datetime,
    session: "SessionState",
    cfg: RuleConfig,
    rng: random.Random | None = None,
) -> Decision:
    """判断此刻该不该主动开口。返回的 act=False 时 reason 是被哪条闸门拦的。"""
    rng = rng or random.Random()

    if not session.enabled:
        return Decision(False, "disabled")
    if session.last_activity_ts is None:
        return Decision(False, "no_activity")
    if session.chat_type == "group" and not cfg.allow_group:
        return Decision(False, "group_not_allowed")
    if in_quiet_hours(local_dt.hour * 60 + local_dt.minute, cfg.quiet_start, cfg.quiet_end):
        return Decision(False, "quiet_hours")

    today = local_day_key(local_dt)
    sent_today = session.count_today if session.count_date == today else 0
    if sent_today >= cfg.max_per_day:
        return Decision(False, "daily_limit", {"sent_today": sent_today})

    if session.last_proactive_ts is not None:
        since_last = now_ts - session.last_proactive_ts
        if since_last < cfg.min_interval_minutes * 60:
            return Decision(False, "min_interval", {"since_last_s": int(since_last)})

    if session.unanswered >= cfg.unanswered_stop:
        return Decision(False, "unanswered_cooldown", {"unanswered": session.unanswered})

    idle_s = now_ts - session.last_activity_ts
    need_s = cfg.idle_min_seconds * (cfg.backoff_factor ** session.unanswered)
    if idle_s < need_s:
        return Decision(
            False,
            "waiting",
            {"idle_s": int(idle_s), "need_s": int(need_s), "unanswered": session.unanswered},
        )

    if idle_s > cfg.active_window_hours * 3600:
        return Decision(False, "stale", {"idle_s": int(idle_s)})

    # 用户刚发了消息、机器人还没回，这时候插话会和正常回复撞在一起。
    if (
        session.last_user_ts is not None
        and session.last_bot_ts is not None
        and session.last_user_ts > session.last_bot_ts
    ):
        return Decision(False, "bot_hasnt_replied")

    reason = choose_reason(session, local_dt, cfg, rng)
    return Decision(
        True,
        reason,
        {"idle_s": int(idle_s), "sent_today": sent_today, "unanswered": session.unanswered},
    )

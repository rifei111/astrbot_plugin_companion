"""会话状态与决策记录的数据结构。

纯 Python，不依赖 AstrBot，可以在测试里直接构造。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 每个会话保留的最近消息条数。太多会让 prompt 变长、成本上去；太少接不上话。
MAX_RECENT = 12


@dataclass
class RecentMessage:
    """最近一条收发的消息，用于让主动开口能接上上一轮。"""

    role: str  # "user" 或 "bot"
    text: str
    ts: float

    def to_dict(self) -> dict:
        return {"role": self.role, "text": self.text, "ts": self.ts}

    @classmethod
    def from_dict(cls, raw: dict) -> "RecentMessage":
        return cls(
            role=str(raw.get("role", "user")),
            text=str(raw.get("text", ""))[:200],
            ts=float(raw.get("ts", 0.0)),
        )


@dataclass
class SessionState:
    """一个会话（私聊或群）的主动消息状态。

    umo 是 AstrBot 的 unified_msg_origin，主动发消息时靠它找回平台和会话。
    """

    umo: str
    chat_type: str = "private"
    title: str = ""
    enabled: bool = False
    last_user_ts: float | None = None
    last_bot_ts: float | None = None
    last_activity_ts: float | None = None
    recent: list[RecentMessage] = field(default_factory=list)
    count_date: str = ""
    count_today: int = 0
    last_proactive_ts: float | None = None
    # 连续发了主动消息、用户一直没回的次数，用来做退避和冷却。
    unanswered: int = 0
    sent_total: int = 0
    replied_total: int = 0
    created_ts: float = 0.0

    # 只在内存里用一个 tick 周期，避免同一会话被重复调度；不落盘。
    sending: bool = field(default=False, compare=False, repr=False)

    def add_recent(self, role: str, text: str, ts: float) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.recent.append(RecentMessage(role=role, text=text[:200], ts=ts))
        if len(self.recent) > MAX_RECENT:
            self.recent = self.recent[-MAX_RECENT:]

    def touch(self, ts: float, role: str) -> None:
        """记录一次收发，同时维护"用户回没回"的计数。"""
        if role == "user":
            self.last_user_ts = ts
            self.unanswered = 0
        else:
            self.last_bot_ts = ts
        self.last_activity_ts = ts

    def to_dict(self) -> dict:
        return {
            "umo": self.umo,
            "chat_type": self.chat_type,
            "title": self.title,
            "enabled": self.enabled,
            "last_user_ts": self.last_user_ts,
            "last_bot_ts": self.last_bot_ts,
            "last_activity_ts": self.last_activity_ts,
            "recent": [m.to_dict() for m in self.recent],
            "count_date": self.count_date,
            "count_today": self.count_today,
            "last_proactive_ts": self.last_proactive_ts,
            "unanswered": self.unanswered,
            "sent_total": self.sent_total,
            "replied_total": self.replied_total,
            "created_ts": self.created_ts,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SessionState":
        state = cls(
            umo=str(raw.get("umo", "")),
            chat_type=str(raw.get("chat_type", "private")),
            title=str(raw.get("title", "")),
            enabled=bool(raw.get("enabled", False)),
            count_date=str(raw.get("count_date", "")),
            count_today=int(raw.get("count_today", 0) or 0),
            unanswered=int(raw.get("unanswered", 0) or 0),
            sent_total=int(raw.get("sent_total", 0) or 0),
            replied_total=int(raw.get("replied_total", 0) or 0),
            created_ts=float(raw.get("created_ts", 0.0) or 0.0),
        )
        for key in ("last_user_ts", "last_bot_ts", "last_activity_ts", "last_proactive_ts"):
            value = raw.get(key)
            setattr(state, key, float(value) if value is not None else None)
        recent = raw.get("recent") or []
        if isinstance(recent, list):
            state.recent = [
                RecentMessage.from_dict(item)
                for item in recent
                if isinstance(item, dict)
            ][-MAX_RECENT:]
        return state

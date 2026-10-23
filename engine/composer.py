"""把"该开口了"变成一句真正像人说的话。

两个来源：配置里的模板（不花钱、离线可用），或者让模型现写一句（更像人、有
上下文）。不管哪种，出去之前都要过一遍清洗和分条——模型很爱写"我：xxx"、爱
用列表、爱一次说三句，这些在聊天窗口里一眼假。
"""

from __future__ import annotations

import random
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .models import SessionState

DEFAULT_TEMPLATES: dict[str, list[str]] = {
    "care": ["在忙吗？", "突然想起来，你吃了吗", "忙完了没"],
    "followup": ["你刚才说的那个，后来呢", "对了，上次那事怎么样了"],
    "impulse": ["没什么事", "就是突然想找你说话", "在干嘛呢"],
    "night": ["还没睡啊", "这么晚还不睡", "睡不着？"],
    "fallback": ["嗯……在吗", "刚才想说什么又忘了"],
}

DEFAULT_SYSTEM_PROMPT = """你在用手机和一个很熟的人聊天，现在由你先开口。

要求：
- 只说一句话，最多两句，像随手在微信里打的那种。
- 口语，短句。不要用"首先/其次/总的来说"这类词，不要用列表，不要堆表情。
- 不提 AI、助手、模型，不解释你为什么突然开口。
- 不要连续追问，不要一次问好几个问题。
- 只输出要发出去的那句话本身，不要带引号、不要带"我："。"""

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_REASON_LINE = {
    "care": "有一会儿没说话了，你想随便说一句。",
    "followup": "上一轮话题还没说完，你想接着问一句。",
    "impulse": "没什么理由，就是突然想找他说话。",
    "night": "很晚了，你还没睡，想问问他睡了没。",
}


def pick_template(
    reason: str,
    templates: dict[str, list[str]] | None = None,
    rng: random.Random | None = None,
) -> str:
    rng = rng or random.Random()
    pool = (templates or DEFAULT_TEMPLATES).get(reason) or (templates or DEFAULT_TEMPLATES).get(
        "fallback"
    ) or DEFAULT_TEMPLATES["fallback"]
    return rng.choice(list(pool))


def sanitize(text: str, max_chars: int = 120) -> str:
    """把模型输出清洗成一条能直接发出去的消息。"""
    if not text:
        return ""
    out = str(text).strip()
    # 去掉常见的"角色前缀"
    out = re.sub(r"^(我|AI|助理|助手|Companion)\s*[:：]\s*", "", out, flags=re.IGNORECASE)
    # 去掉整段包裹的引号
    out = out.strip("「」『』“”\"'` ")
    lines: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        # 去掉 markdown 列表符
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.、)]\s*", "", line)
        lines.append(line)
    out = " ".join(lines).strip()
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"。{2,}", "。", out)
    if len(out) > max_chars:
        cut = out[:max_chars]
        for mark in ("。", "！", "？", "，", "…", "~"):
            pos = cut.rfind(mark)
            if pos >= max_chars * 0.6:
                cut = cut[: pos + 1]
                break
        out = cut.rstrip("，。、 ")
    return out.strip()


def split_bubbles(
    text: str,
    max_bubbles: int = 3,
    rng: random.Random | None = None,
) -> list[str]:
    """决定这一句是发一条还是拆成两三条。

    "拆条"是聊天里最廉价的人味：真人很少把三句话压成一条发出去。但也不能见
    句号就拆，所以短句不拆，长句按标点拆，最多 max_bubbles 条。
    """
    rng = rng or random.Random()
    text = (text or "").strip()
    if not text:
        return []
    if max_bubbles <= 1 or len(text) <= 16 or rng.random() < 0.45:
        return [text]

    pieces = [p.strip() for p in re.split(r"(?<=[。！？!?…~])|\n+", text) if p and p.strip()]
    if len(pieces) < 2:
        pieces = [p.strip() for p in re.split(r"[，,]\s*", text) if p.strip()]
    if len(pieces) < 2:
        return [text]
    if len(pieces) > max_bubbles:
        head = pieces[: max_bubbles - 1]
        tail = "".join(pieces[max_bubbles - 1 :])
        pieces = head + [tail]
    return [p for p in pieces if p]


def _humanize_gap(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{int(seconds // 86400)} 天前"


def build_prompt(
    reason: str,
    session: "SessionState",
    now_local: datetime,
    now_ts: float | None = None,
) -> str:
    """装配给模型看的上下文：现在几点、多久没说话、为什么开口、上一轮聊了什么。"""
    now_ts = now_local.timestamp() if now_ts is None else now_ts
    stamp = now_local.strftime("%Y-%m-%d %H:%M")
    weekday = _WEEKDAYS[now_local.weekday()]
    gap = _humanize_gap(now_ts - session.last_activity_ts) if session.last_activity_ts else "未知"

    lines = [
        "<context>",
        f"现在：{stamp}（{weekday}）",
        f"你们上次说话：{gap}",
        f"你开口的理由：{_REASON_LINE.get(reason, _REASON_LINE['care'])}",
        "</context>",
    ]
    recent = session.recent[-6:]
    if recent:
        lines.append("<recent>")
        for msg in recent:
            who = "他" if msg.role == "user" else "你"
            lines.append(f"{who}：{msg.text}")
        lines.append("</recent>")
    lines.append("直接给出你要发的那句话。")
    return "\n".join(lines)


def build_recent_contexts(session: "SessionState", limit: int = 6) -> list[dict[str, Any]]:
    """把最近消息转成 provider 要的 contexts 格式。"""
    contexts: list[dict[str, Any]] = []
    for msg in session.recent[-limit:]:
        role = "user" if msg.role == "user" else "assistant"
        contexts.append({"role": role, "content": msg.text})
    return contexts


async def compose_llm(
    provider: Any,
    reason: str,
    session: "SessionState",
    now_local: datetime,
    system_prompt: str = "",
    max_chars: int = 120,
    now_ts: float | None = None,
) -> str:
    """让模型写一句；任何异常都返回空串，由调用方退回模板。"""
    if provider is None:
        return ""
    prompt = build_prompt(reason, session, now_local, now_ts)
    system = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    try:
        resp = await provider.text_chat(
            prompt=prompt,
            contexts=build_recent_contexts(session),
            system_prompt=system,
        )
    except Exception:
        return ""
    text = getattr(resp, "completion_text", "") or ""
    return sanitize(text, max_chars=max_chars)

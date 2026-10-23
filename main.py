"""AstrBot 插件入口：拟人主动消息（P0 规则版）。

它只做一件事：在你会话里安静地等一会儿，如果确实过了一段时间没人说话，就在
合适的时刻自己开口一次——有上限、有静默时段、被无视会退避，而且默认对每个
会话单独开关，不会去打扰没同意的群。

所有"该不该开口"的判断都在 engine/rules.py（纯函数，可单测），这里只负责和
AstrBot 打交道：收消息、记账、调模型、发消息、落盘。
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:  # 正常插件加载路径：本包以 data.plugins.<name> 的形式被 import
    from .engine import composer, delivery, rules
    from .engine.models import SessionState
    from .engine.store import StateStore
except ImportError:  # pragma: no cover - 兼容把 main.py 当顶层模块加载
    from engine import composer, delivery, rules
    from engine.models import SessionState
    from engine.store import StateStore

PLUGIN_NAME = "astrbot_plugin_companion"

# 主动发出后，用户在这么久之内回话，就算"这条主动没白发"。
REPLY_WINDOW_SECONDS = 30 * 60


def _default_data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        base = Path(get_astrbot_plugin_data_path())
    except Exception:  # noqa: BLE001 - 老版本 AstrBot 没有这个函数，退回插件目录
        base = Path(__file__).resolve().parent / "data"
    return base / PLUGIN_NAME


class CompanionPlugin(Star):
    """规则驱动的拟人主动消息。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self._store = StateStore(_default_data_dir(), logger=self.logger)
        self._sessions: dict[str, SessionState] = {}
        self._decisions: list[dict] = []
        self._rng = random.Random()
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        self._sessions, self._decisions = self._store.load()
        self.logger.info(
            "拟人主动已加载：%d 个会话，%d 条历史决策",
            len(self._sessions),
            len(self._decisions),
        )
        self._loop_task = asyncio.create_task(self._tick_loop())

    async def terminate(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None
        self._save()

    # ------------------------------------------------------------------ 记录消息

    def _cfg(self) -> dict:
        return self.config if isinstance(self.config, dict) else {}

    def _ensure_session(self, event: AstrMessageEvent) -> SessionState:
        umo = str(event.unified_msg_origin)
        state = self._sessions.get(umo)
        now = time.time()
        if state is None:
            state = SessionState(
                umo=umo,
                chat_type="private" if event.is_private_chat() else "group",
                title=str(event.get_sender_name() or ""),
                created_ts=now,
            )
            state.enabled = bool(
                self._cfg().get("auto_enable_private", False)
                and state.chat_type == "private"
            )
            self._sessions[umo] = state
        return state

    def _mark_replied(self, state: SessionState, now: float) -> None:
        """用户回话时，把最近一次主动记成"被回应"。"""
        if state.last_proactive_ts is None:
            return
        if now - state.last_proactive_ts > REPLY_WINDOW_SECONDS:
            return
        state.replied_total += 1
        state.last_proactive_ts = state.last_proactive_ts  # 保留时间戳，只改计数
        for record in reversed(self._decisions[-20:]):
            if record.get("umo") == state.umo and record.get("sent"):
                record["replied"] = True
                break

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent) -> None:
        """任何消息都记一笔：上次说话时间、最近对话、有没有人回。"""
        try:
            state = self._ensure_session(event)
            now = time.time()
            text = str(event.message_str or "").strip()
            if text:
                self._mark_replied(state, now)
                state.add_recent("user", text, now)
            state.touch(now, "user")
            state.sent_total = state.sent_total  # 保持字段存在，避免旧状态缺少
            self._save()
        except Exception:
            self.logger.exception("记录用户消息失败")

    @filter.after_message_sent()
    async def on_bot_sent(self, event: AstrMessageEvent) -> None:
        """记录机器人正常回复：这也是"上一轮结束"的信号。"""
        try:
            result = event.get_result()
            text = result.get_plain_text() if result is not None else ""
            if not text:
                return
            state = self._ensure_session(event)
            now = time.time()
            state.add_recent("bot", text, now)
            state.touch(now, "bot")
            self._save()
        except Exception:
            self.logger.exception("记录机器人回复失败")

    # ------------------------------------------------------------------ 主动发送

    def _save(self) -> None:
        try:
            self._store.save(self._sessions, self._decisions)
        except Exception:
            self.logger.exception("保存状态失败")

    def _record_decision(
        self,
        umo: str,
        decision: rules.Decision,
        sent: bool,
        text: str = "",
    ) -> None:
        self._decisions.append(
            {
                "ts": time.time(),
                "umo": umo,
                "act": decision.act,
                "reason": decision.reason,
                "explain": decision.explain,
                "sent": sent,
                "text": text,
                "replied": False,
            }
        )
        del self._decisions[:-500]

    async def _get_provider(self, umo: str) -> Any:
        provider_id = str(self._cfg().get("provider_id") or "").strip()
        try:
            if provider_id:
                return self.context.get_provider_by_id(provider_id=provider_id)
            return await self.context.get_using_provider_async(umo=umo)
        except Exception:
            self.logger.warning("获取 LLM Provider 失败，退回模板", exc_info=True)
            return None

    async def _compose(self, state: SessionState, reason: str) -> str:
        cfg = self._cfg()
        templates = cfg.get("templates") or None
        text = ""
        if bool(cfg.get("use_llm", True)):
            provider = await self._get_provider(state.umo)
            if provider is not None:
                text = await composer.compose_llm(
                    provider,
                    reason,
                    state,
                    datetime.now(),
                    system_prompt=str(cfg.get("llm_system_prompt") or ""),
                    max_chars=int(cfg.get("max_chars", 120) or 120),
                )
        if not text:
            text = composer.pick_template(reason, templates, self._rng)
        return composer.sanitize(text, max_chars=int(cfg.get("max_chars", 120) or 120))

    async def _send_text(self, umo: str, text: str) -> bool:
        try:
            chain = MessageChain().message(text)
            return bool(await self.context.send_message(umo, chain))
        except Exception:
            self.logger.exception("主动消息发送失败：%s", umo)
            return False

    async def _run_proactive(self, umo: str, decision: rules.Decision) -> None:
        state = self._sessions.get(umo)
        if state is None or umo in self._inflight:
            return
        cfg = self._cfg()
        self._inflight.add(umo)
        state.sending = True
        try:
            text = await self._compose(state, decision.reason)
            if not text:
                return
            sent = await delivery.deliver(
                umo,
                text,
                self._send_text,
                delivery.DeliveryConfig.from_config(cfg),
                self._rng,
            )
            now = time.time()
            today = datetime.now().strftime("%Y-%m-%d")
            if state.count_date != today:
                state.count_date = today
                state.count_today = 0
            if sent:
                state.count_today += 1
                state.sent_total += 1
                state.last_proactive_ts = now
                state.unanswered += 1
                for piece in sent:
                    state.add_recent("bot", piece, now)
                state.touch(now, "bot")
            self._record_decision(umo, decision, bool(sent), text)
            self._save()
        except Exception:
            self.logger.exception("主动消息流程异常：%s", umo)
        finally:
            state.sending = False
            self._inflight.discard(umo)

    # ------------------------------------------------------------------ tick 循环

    async def _tick_loop(self) -> None:
        while True:
            try:
                interval = float(self._cfg().get("tick_seconds", 30) or 30)
                await asyncio.sleep(max(5.0, interval))
                await self._tick_once()
            except asyncio.CancelledError:
                return
            except Exception:
                self.logger.exception("主动消息 tick 出错")

    async def _tick_once(self) -> None:
        cfg = self._cfg()
        if not bool(cfg.get("enabled", True)):
            return
        rule_cfg = rules.RuleConfig.from_config(cfg)
        now = time.time()
        now_local = datetime.now()
        for umo, state in list(self._sessions.items()):
            if umo in self._inflight or state.sending:
                continue
            decision = rules.evaluate(now, now_local, state, rule_cfg, self._rng)
            if not decision.act:
                continue
            if bool(cfg.get("dry_run", False)):
                # 影子模式：只记账不发，用来先观察它会挑什么时机。
                self._record_decision(umo, decision, sent=False)
                self._save()
                continue
            task = asyncio.create_task(self._run_proactive(umo, decision))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ 指令

    def _status_text(self, state: SessionState) -> str:
        cfg = self._cfg()
        rule_cfg = rules.RuleConfig.from_config(cfg)
        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        sent_today = state.count_today if state.count_date == today else 0
        last = (
            composer._humanize_gap(now - state.last_proactive_ts)
            if state.last_proactive_ts
            else "还没发过"
        )
        reply_rate = (
            f"{state.replied_total}/{state.sent_total}" if state.sent_total else "—"
        )
        on_off = "开" if state.enabled else "关"
        return (
            "📮 拟人主动状态\n"
            f"全局开关：{'开' if cfg.get('enabled', True) else '关'}"
            f"{'（影子模式）' if cfg.get('dry_run', False) else ''}\n"
            f"本会话：{on_off}　类型：{'私聊' if state.chat_type == 'private' else '群聊'}\n"
            f"今天已发：{sent_today}/{rule_cfg.max_per_day}\n"
            f"最近一次主动：{last}\n"
            f"被回应：{reply_rate}\n"
            f"静默时段：{rule_cfg.quiet_start}–{rule_cfg.quiet_end}，间隔 ≥{rule_cfg.min_interval_minutes} 分钟\n"
            f"生成方式：{'LLM' if cfg.get('use_llm', True) else '模板'}\n"
            "指令：/主动开 /主动关 /主动试 /主动日志"
        )

    @filter.command("主动", alias={"companion"})
    async def cmd_status(self, event: AstrMessageEvent):
        """/主动 —— 查看当前会话的主动消息状态。"""
        state = self._ensure_session(event)
        return event.plain_result(self._status_text(state))

    @filter.command("主动开")
    async def cmd_enable(self, event: AstrMessageEvent):
        """/主动开 —— 在本会话开启主动消息。"""
        state = self._ensure_session(event)
        if state.chat_type == "group" and not bool(
            self._cfg().get("allow_group", False)
        ):
            return event.plain_result(
                "群聊默认不开启主动消息（需要管理员在配置里打开 allow_group）。"
            )
        state.enabled = True
        if not state.last_activity_ts:
            state.touch(time.time(), "user")
        if state.count_date != datetime.now().strftime("%Y-%m-%d"):
            state.count_date = datetime.now().strftime("%Y-%m-%d")
            state.count_today = 0
        self._save()
        return event.plain_result(
            f"已开启。规则：隔 {int(rules.RuleConfig.from_config(self._cfg()).idle_min_seconds)} 秒以上没动静才可能开口，"
            f"每天最多 {rules.RuleConfig.from_config(self._cfg()).max_per_day} 次。"
        )

    @filter.command("主动关")
    async def cmd_disable(self, event: AstrMessageEvent):
        """/主动关 —— 在本会话关闭主动消息。"""
        state = self._ensure_session(event)
        state.enabled = False
        self._save()
        return event.plain_result("已关闭。这个会话里它不会再主动开口。")

    @filter.command("主动试")
    async def cmd_preview(self, event: AstrMessageEvent):
        """/主动试 —— 预览一句它现在会说什么（不真的发出去）。"""
        state = self._ensure_session(event)
        decision = rules.evaluate(
            time.time(),
            datetime.now(),
            state,
            rules.RuleConfig.from_config(self._cfg()),
            self._rng,
        )
        reason = decision.reason if decision.act else "care"
        text = await self._compose(state, reason)
        return event.plain_result(
            f"（预览，未发送）理由：{reason}\n{text or '（生成失败）'}"
        )

    @filter.command("主动日志")
    async def cmd_log(self, event: AstrMessageEvent):
        """/主动日志 —— 看最近 5 条判定记录。"""
        state = self._ensure_session(event)
        records = [r for r in self._decisions if r.get("umo") == state.umo][-5:]
        if not records:
            return event.plain_result("还没有判定记录。")
        lines = ["最近判定："]
        for record in records:
            stamp = datetime.fromtimestamp(float(record.get("ts", 0))).strftime(
                "%m-%d %H:%M"
            )
            flag = "发" if record.get("sent") else "未发"
            mark = " ✓" if record.get("replied") else ""
            lines.append(f"{stamp} [{flag}] {record.get('reason')}{mark}")
        return event.plain_result("\n".join(lines))

    @filter.command("主动全局")
    async def cmd_global(self, event: AstrMessageEvent):
        """/主动全局 —— 管理员切换插件总开关。"""
        if not event.is_admin():
            return event.plain_result("这个指令只有管理员能用。")
        cfg = self.config
        if not isinstance(cfg, dict):
            return event.plain_result("当前配置不可写。")
        current = bool(cfg.get("enabled", True))
        cfg["enabled"] = not current
        try:
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
        except Exception:
            self.logger.exception("保存全局配置失败")
        return event.plain_result(
            "全局主动消息已" + ("关闭" if current else "开启") + "。"
        )

"""状态落盘。

AstrBot 重启、插件重载都很常见，所以状态必须存到 data 目录、并且能容忍写一半
的文件：磁盘满、断电、进程被杀都可能留下半截 JSON。这里的做法是"写临时文件 +
原子替换"，读到坏文件就备份后重建，绝不让插件因为状态文件坏掉而起不来。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .models import SessionState

# 决策记录只用来回看和调参，不必无限长；太多会让状态文件读得越来越慢。
MAX_DECISIONS = 500


class StateStore:
    """会话状态与决策日志的读写。"""

    def __init__(self, data_dir: Path, logger: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "state.json"
        self.logger = logger

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self.logger
        if logger is not None and hasattr(logger, level):
            try:
                getattr(logger, level)(message, *args)
                return
            except Exception:  # pragma: no cover - 日志本身不该让流程失败
                pass

    def load(self) -> tuple[dict[str, SessionState], list[dict]]:
        """读状态。任何损坏都退回空状态，并把坏文件留一份现场。"""
        if not self.path.exists():
            return {}, []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._backup_corrupt()
            self._log("warning", "状态文件损坏，已重建：%s", exc)
            return {}, []

        sessions: dict[str, SessionState] = {}
        for item in (raw.get("sessions") or {}).values() if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("umo"):
                continue
            try:
                state = SessionState.from_dict(item)
            except (TypeError, ValueError):
                continue
            sessions[state.umo] = state

        decisions: list[dict] = []
        if isinstance(raw, dict) and isinstance(raw.get("decisions"), list):
            decisions = [d for d in raw["decisions"] if isinstance(d, dict)][-MAX_DECISIONS:]
        return sessions, decisions

    def save(self, sessions: dict[str, SessionState], decisions: list[dict]) -> None:
        """原子写盘：先写同目录临时文件，再 os.replace 覆盖。"""
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "sessions": {umo: state.to_dict() for umo, state in sessions.items()},
            "decisions": decisions[-MAX_DECISIONS:],
        }
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".state-", suffix=".tmp", dir=str(self.data_dir)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fp:
                    json.dump(payload, fp, ensure_ascii=False, indent=1)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_name, self.path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            self._log("error", "状态保存失败：%s", exc)

    def _backup_corrupt(self) -> None:
        try:
            backup = self.path.with_name(f"state.corrupt-{int(time.time())}.json")
            os.replace(self.path, backup)
        except OSError:
            pass

"""互动插件的数据存储层。

统一封装基于 JSON 文件的持久化读写，按会话（群/私聊）隔离数据，
并提供签到、积分、统计等高频操作的原子访问方法。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

# 数据落盘节流：距上次写盘不足该秒数时，仅标记 dirty，由下一次写入或定时任务统一落盘。
_FLUSH_INTERVAL = 5.0


class InteractionStore:
    """按会话隔离的互动数据仓库。

    数据以 ``data/interaction/<platform>_<session>.json`` 存放，
    结构形如::

        {
          "users": {"<uid>": {"balance": 0, "sign_date": "", "streak": 0,
                              "last_sign_ts": 0, "total_sign": 0}},
          "polls": {"<poll_id>": {...}},
          "meta": {"created_at": 0, "updated_at": 0}
        }

    Attributes:
        data_dir: 数据根目录。
    """

    def __init__(self, data_dir: Path) -> None:
        """初始化仓库。

        Args:
            data_dir: 数据根目录，通常为 AstrBot data 目录下的插件子目录。
        """
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}
        self._dirty: set[str] = set()
        self._last_flush: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _safe_name(session_key: str) -> str:
        """将会话标识转换为安全文件名。

        Args:
            session_key: 形如 ``aiocqhttp_group_123456`` 的会话标识。

        Returns:
            仅含字母数字、下划线、中划线的文件名（不含扩展名）。
        """
        cleaned = "".join(
            ch if ch.isalnum() or ch in "_-" else "_" for ch in session_key
        )
        return cleaned[:120] or "default"

    def _path(self, session_key: str) -> Path:
        """获取某会话对应的数据文件路径。

        Args:
            session_key: 会话标识。

        Returns:
            数据文件路径。
        """
        return self.data_dir / f"{self._safe_name(session_key)}.json"

    def _load(self, session_key: str) -> dict[str, Any]:
        """从磁盘或缓存加载会话数据。

        Args:
            session_key: 会话标识。

        Returns:
            会话数据字典，结构非法时回退为空结构。
        """
        if session_key in self._cache:
            return self._cache[session_key]
        path = self._path(session_key)
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[互动] 读取数据文件失败 {path}: {e}，将重建。")
                data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("users", {})
        data.setdefault("polls", {})
        data.setdefault("meta", {"created_at": time.time()})
        self._cache[session_key] = data
        return data

    def _mark_dirty(self, session_key: str) -> None:
        """标记会话数据待落盘。

        Args:
            session_key: 会话标识。
        """
        self._dirty.add(session_key)

    async def save(self, session_key: str, force: bool = False) -> None:
        """将会话数据写回磁盘。

        Args:
            session_key: 会话标识。
            force: 为 True 时忽略节流立即落盘。
        """
        if session_key not in self._dirty and not force:
            return
        async with self._lock:
            now = time.time()
            if (
                not force
                and now - self._last_flush.get(session_key, 0) < _FLUSH_INTERVAL
            ):
                return
            data = self._load(session_key)
            data.setdefault("meta", {})["updated_at"] = now
            path = self._path(session_key)
            try:
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                tmp.replace(path)
                self._dirty.discard(session_key)
                self._last_flush[session_key] = now
            except OSError as e:
                logger.error(f"[互动] 写入数据文件失败 {path}: {e}")

    async def flush_all(self) -> None:
        """将所有待落盘的会话数据写入磁盘（插件卸载/定时任务调用）。"""
        for session_key in list(self._dirty):
            await self.save(session_key, force=True)

    def get_user(self, session_key: str, user_id: str) -> dict[str, Any]:
        """获取（不存在则创建）某用户的互动档案。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。

        Returns:
            用户档案字典，含 ``balance``、``sign_date``、``streak`` 等字段。
        """
        data = self._load(session_key)
        user = data["users"].get(user_id)
        if not isinstance(user, dict):
            user = {
                "balance": 0,
                "sign_date": "",
                "streak": 0,
                "best_streak": 0,
                "last_sign_ts": 0,
                "total_sign": 0,
                "lottery_count": 0,
                "guess_win": 0,
                "chain_win": 0,
            }
            data["users"][user_id] = user
            self._mark_dirty(session_key)
        for key, default in (
            ("balance", 0),
            ("sign_date", ""),
            ("streak", 0),
            ("best_streak", 0),
            ("last_sign_ts", 0),
            ("total_sign", 0),
            ("lottery_count", 0),
            ("guess_win", 0),
            ("chain_win", 0),
        ):
            user.setdefault(key, default)
        return user

    def add_balance(self, session_key: str, user_id: str, amount: int) -> int:
        """增减用户积分，返回变更后的余额。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。
            amount: 变化量，可为负数；余额不会低于 0。

        Returns:
            变更后的积分余额。
        """
        user = self.get_user(session_key, user_id)
        user["balance"] = max(0, int(user["balance"]) + int(amount))
        self._mark_dirty(session_key)
        return user["balance"]

    def top_users(
        self, session_key: str, by: str = "balance", limit: int = 10
    ) -> list[tuple[str, int]]:
        """获取排行榜。

        Args:
            session_key: 会话标识。
            by: 排序字段，如 ``balance``、``total_sign``、``lottery_count``。
            limit: 返回条数。

        Returns:
            ``(user_id, value)`` 列表，按值降序。
        """
        users = self._load(session_key)["users"]
        rows: list[tuple[str, int]] = []
        for uid, info in users.items():
            if isinstance(info, dict):
                rows.append((uid, int(info.get(by, 0) or 0)))
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows[:limit]

    def list_users(self, session_key: str) -> dict[str, Any]:
        """返回会话下全部用户档案。

        Args:
            session_key: 会话标识。

        Returns:
            用户 ID 到档案的映射。
        """
        return self._load(session_key)["users"]

    def polls(self, session_key: str) -> dict[str, Any]:
        """返回会话下的投票数据。

        Args:
            session_key: 会话标识。

        Returns:
            投票 ID 到投票数据的映射。
        """
        return self._load(session_key)["polls"]

    def touch(self, session_key: str) -> None:
        """标记会话数据已变更，等待落盘。

        Args:
            session_key: 会话标识。
        """
        self._mark_dirty(session_key)

"""互动插件的数据存储层。

按「平台 + 会话」隔离落盘，提供签到、积分、统计等高频访问接口。

设计要点：
- 内存缓存 + 脏标记，读取零 IO；
- 写盘节流 + 合并，避免高频小写入；
- **JSON 序列化与文件 IO 全部放到线程池**，不阻塞事件循环；
- 原子替换（临时文件 + ``os.replace``），避免半截文件；
- 用户档案自动补齐字段，旧数据结构升级不丢数据；
- 全局限流：单会话用户数、总会话数都有上限，防止内存/磁盘被刷爆。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

# 距上次写盘不足该秒数时只打脏标记，由定时任务或下次写入统一落盘。
_FLUSH_INTERVAL = 3.0
# 单会话最多保留多少用户档案，超出后按最后活跃时间淘汰。
_MAX_USERS_PER_SESSION = 20000
# 最多同时缓存多少个会话，超出后按最后访问时间淘汰（不影响磁盘数据）。
_MAX_CACHED_SESSIONS = 512

# 用户档案字段及默认值。新增字段只需改这一处。
USER_FIELDS: dict[str, Any] = {
    "balance": 0,
    "sign_date": "",
    "streak": 0,
    "best_streak": 0,
    "last_sign_ts": 0,
    "total_sign": 0,
    "lottery_count": 0,
    "guess_win": 0,
    "chain_win": 0,
    "last_lottery_ts": 0,
    "title": "",
    "titles": [],
    "daily_date": "",
    "daily_balance": 0,
    "rob_win": 0,
    "rob_lose": 0,
    "last_active": 0,
    "name": "",
    "quest_date": "",
    "quest_done": [],
    "checkin_reward": 0,
    "dice_count": 0,
    "lucky_hit": 0,
    "lucky_last": "",
}


class InteractionStore:
    """按会话隔离的互动数据仓库。

    数据以 ``<data_dir>/<平台>_<会话>.json`` 存放，结构形如::

        {
          "users": {"<uid>": {...}},
          "polls": {"<poll_id>": {...}},
          "meta": {"created_at": 0, "updated_at": 0}
        }

    Attributes:
        data_dir: 数据根目录。
    """

    def __init__(self, data_dir: Path) -> None:
        """初始化仓库。

        Args:
            data_dir: 数据根目录。
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}
        self._dirty: set[str] = set()
        self._last_flush: dict[str, float] = {}
        self._accessed: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 路径与序列化

    @staticmethod
    def _safe_name(session_key: str) -> str:
        """把会话标识转成安全文件名。

        Args:
            session_key: 会话标识，如 ``aiocqhttp_group_123456``。

        Returns:
            仅含字母数字、下划线、中划线的文件名（不含扩展名）。
        """
        cleaned = "".join(
            ch if ch.isalnum() or ch in "_-" else "_" for ch in session_key
        )
        return cleaned[:120] or "default"

    def _path(self, session_key: str) -> Path:
        """获取某会话的数据文件路径。

        Args:
            session_key: 会话标识。

        Returns:
            数据文件路径。
        """
        return self.data_dir / f"{self._safe_name(session_key)}.json"

    @staticmethod
    def _normalize(data: Any) -> dict[str, Any]:
        """把任意读到的数据结构规整为合法结构。

        Args:
            data: 原始数据。

        Returns:
            含 ``users`` / ``polls`` / ``meta`` 三键的字典。
        """
        if not isinstance(data, dict):
            data = {}
        users = data.get("users")
        if not isinstance(users, dict):
            users = {}
        # 清掉历史脏数据，保证后续访问类型安全
        data["users"] = {
            str(uid): info for uid, info in users.items() if isinstance(info, dict)
        }
        polls = data.get("polls")
        if not isinstance(polls, dict):
            polls = {}
        data["polls"] = {
            str(pid): info for pid, info in polls.items() if isinstance(info, dict)
        }
        meta = data.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("created_at", time.time())
        data["meta"] = meta
        return data

    def _evict_cache(self) -> None:
        """缓存会话数超限时，淘汰最久未访问且已落盘的会话。"""
        if len(self._cache) <= _MAX_CACHED_SESSIONS:
            return
        candidates = sorted(
            (k for k in self._cache if k not in self._dirty),
            key=lambda k: self._accessed.get(k, 0),
        )
        for key in candidates[: len(self._cache) - _MAX_CACHED_SESSIONS]:
            self._cache.pop(key, None)
            self._accessed.pop(key, None)

    def _load(self, session_key: str) -> dict[str, Any]:
        """从缓存或磁盘加载会话数据（同步，仅供内部非阻塞路径外的读取使用）。

        Args:
            session_key: 会话标识。

        Returns:
            规整后的会话数据。
        """
        self._accessed[session_key] = time.time()
        cached = self._cache.get(session_key)
        if cached is not None:
            return cached
        data: Any = {}
        path = self._path(session_key)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                logger.warning(f"[互动] 读取数据文件失败 {path}: {e}，将重建。")
                data = {}
        normalized = self._normalize(data)
        self._cache[session_key] = normalized
        self._evict_cache()
        return normalized

    # ------------------------------------------------------------------ 落盘

    def _mark_dirty(self, session_key: str) -> None:
        """标记会话数据待落盘。

        Args:
            session_key: 会话标识。
        """
        self._dirty.add(session_key)

    @staticmethod
    def _write_sync(path: Path, payload: str) -> None:
        """原子写入文本文件（在线程池中执行）。

        流程为 ``mkstemp -> chmod -> 写 -> fsync -> os.replace -> fsync(dir)``。
        最后一步对父目录做 ``fsync``，保证 ``rename`` 本身也落盘，
        极端断电场景下不会出现「文件没了」的空窗。

        Args:
            path: 目标路径。
            payload: 文本内容。
        """
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".tmp_", suffix=".json"
        )
        try:
            # mkstemp 默认 0600，这里放宽到 0640：同组运维账号可读可备，
            # 又不像 0644 那样对全机所有用户敞开
            os.chmod(tmp_name, 0o640)
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_name, path)
            # 目录项本身也刷盘，避免 rename 在崩溃时丢失
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    async def save(self, session_key: str, force: bool = False) -> None:
        """将会话数据写回磁盘。

        序列化与文件 IO 都在线程池执行，避免阻塞事件循环。

        Args:
            session_key: 会话标识。
            force: 为 ``True`` 时忽略节流立即落盘。
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
            data = self._cache.get(session_key)
            if data is None:
                self._dirty.discard(session_key)
                return
            data.setdefault("meta", {})["updated_at"] = now
            path = self._path(session_key)
            try:
                payload = await asyncio.to_thread(json.dumps, data, ensure_ascii=False)
                await asyncio.to_thread(self._write_sync, path, payload)
                self._dirty.discard(session_key)
                self._last_flush[session_key] = now
            except (OSError, TypeError, ValueError) as e:
                logger.error(f"[互动] 写入数据文件失败 {path}: {e}")

    async def flush_all(self) -> None:
        """把所有待落盘的会话写入磁盘（卸载/定时任务调用）。"""
        for session_key in list(self._dirty):
            await self.save(session_key, force=True)

    async def close(self) -> None:
        """刷盘并释放缓存（插件卸载/重载时调用）。"""
        await self.flush_all()
        self._cache.clear()
        self._accessed.clear()
        self._dirty.clear()
        self._last_flush.clear()

    # ------------------------------------------------------------------ 用户档案

    def get_user(self, session_key: str, user_id: str) -> dict[str, Any]:
        """获取（不存在则创建）某用户的互动档案。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。

        Returns:
            用户档案字典，字段缺失时会被补齐。
        """
        data = self._load(session_key)
        users = data["users"]
        uid = str(user_id)
        user = users.get(uid)
        if not isinstance(user, dict):
            if len(users) >= _MAX_USERS_PER_SESSION:
                # 超出上限：淘汰最久未活跃的用户，避免单群把内存撑爆
                stale = sorted(
                    users.items(), key=lambda kv: int(kv[1].get("last_active", 0) or 0)
                )[: max(1, len(users) // 20)]
                for old_uid, _ in stale:
                    users.pop(old_uid, None)
                self._mark_dirty(session_key)
            user = {}
            users[uid] = user
        for key, default in USER_FIELDS.items():
            if key not in user:
                user[key] = list(default) if isinstance(default, list) else default
        user["last_active"] = int(time.time())
        self._mark_dirty(session_key)
        return user

    def peek_user(self, session_key: str, user_id: str) -> dict[str, Any] | None:
        """只读获取用户档案，不存在时返回 ``None``（不创建、不标脏）。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。

        Returns:
            用户档案，或 ``None``。
        """
        user = self._load(session_key)["users"].get(str(user_id))
        return user if isinstance(user, dict) else None

    def add_balance(self, session_key: str, user_id: str, amount: int) -> int:
        """增减用户积分。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。
            amount: 变化量，可为负数；余额不会低于 0。

        Returns:
            变更后的余额。
        """
        user = self.get_user(session_key, user_id)
        user["balance"] = max(0, int(user["balance"]) + int(amount))
        self._mark_dirty(session_key)
        return user["balance"]

    def transfer(
        self, session_key: str, src: str, dst: str, amount: int
    ) -> tuple[bool, str]:
        """用户间转账（原子，同一会话内）。

        Args:
            session_key: 会话标识。
            src: 付款方 ID。
            dst: 收款方 ID。
            amount: 数量。

        Returns:
            ``(是否成功, 提示)``。
        """
        if amount <= 0:
            return False, "转账数量必须大于 0。"
        if str(src) == str(dst):
            return False, "不能给自己转账哦。"
        payer = self.get_user(session_key, src)
        if int(payer["balance"]) < amount:
            return False, f"余额不足，当前只有 {payer['balance']}。"
        payer["balance"] = int(payer["balance"]) - amount
        receiver = self.get_user(session_key, dst)
        receiver["balance"] = int(receiver["balance"]) + amount
        self._mark_dirty(session_key)
        return True, "ok"

    def top_users(
        self, session_key: str, by: str = "balance", limit: int = 10
    ) -> list[tuple[str, int]]:
        """获取排行榜。

        Args:
            session_key: 会话标识。
            by: 排序字段。
            limit: 返回条数。

        Returns:
            ``(user_id, value)`` 列表，按值降序。
        """
        users = self._load(session_key)["users"]
        rows: list[tuple[str, int]] = []
        for uid, info in users.items():
            try:
                value = int(info.get(by, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                rows.append((uid, value))
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows[:limit]

    def values_of(self, session_key: str, by: str = "balance") -> list[int]:
        """取出会话内某维度的全部非零数值（用于算百分位）。

        Args:
            session_key: 会话标识。
            by: 字段名。

        Returns:
            数值列表。
        """
        users = self._load(session_key)["users"]
        values: list[int] = []
        for info in users.values():
            try:
                value = int(info.get(by, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                values.append(value)
        return values

    def rank_of(self, session_key: str, user_id: str, by: str = "balance") -> int:
        """返回某用户在某维度上的名次（从 1 开始），没数据时返回 0。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。
            by: 字段名。

        Returns:
            名次；该用户数值为 0 时返回 0。
        """
        user = self.peek_user(session_key, user_id)
        if not user:
            return 0
        try:
            mine = int(user.get(by, 0) or 0)
        except (TypeError, ValueError):
            return 0
        if mine <= 0:
            return 0
        return sum(1 for v in self.values_of(session_key, by) if v > mine) + 1

    def rename(self, session_key: str, user_id: str, name: str) -> None:
        """记录用户昵称，便于排行榜展示名字。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。
            name: 昵称。
        """
        if not name:
            return
        user = self.get_user(session_key, user_id)
        if user.get("name") != name:
            user["name"] = name
            self._mark_dirty(session_key)

    def display_name(self, session_key: str, user_id: str) -> str:
        """返回用户展示名，优先昵称，其次 ID。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。

        Returns:
            展示名。
        """
        user = self.peek_user(session_key, user_id)
        if user and user.get("name"):
            return str(user["name"])
        return str(user_id)

    def reset_user(self, session_key: str, user_id: str) -> None:
        """清空某用户的档案。

        Args:
            session_key: 会话标识。
            user_id: 用户 ID。
        """
        user = self.get_user(session_key, user_id)
        user.clear()
        user.update(
            {k: (list(v) if isinstance(v, list) else v) for k, v in USER_FIELDS.items()}
        )
        self._mark_dirty(session_key)

    # ------------------------------------------------------------------ 投票

    def polls(self, session_key: str) -> dict[str, Any]:
        """返回会话下的全部投票。

        Args:
            session_key: 会话标识。

        Returns:
            投票 ID 到投票数据的映射。
        """
        return self._load(session_key)["polls"]

    def add_poll(self, session_key: str, poll: dict[str, Any]) -> None:
        """新增一个投票并标脏。

        Args:
            session_key: 会话标识。
            poll: 投票数据，需含 ``id``。
        """
        self._load(session_key)["polls"][str(poll["id"])] = poll
        self._mark_dirty(session_key)

    def prune_polls(self, ttl: float, now: float | None = None) -> int:
        """清理所有会话中超过 TTL 的投票。

        Args:
            ttl: 投票保留秒数。
            now: 当前时间戳。

        Returns:
            清理掉的投票数量。
        """
        now = time.time() if now is None else now
        removed = 0
        for session_key, data in self._cache.items():
            polls = data["polls"]
            expired = [
                pid
                for pid, poll in polls.items()
                if now - float(poll.get("started_at", now)) > ttl
            ]
            for pid in expired:
                polls.pop(pid, None)
                removed += 1
            if expired:
                self._mark_dirty(session_key)
        return removed

    def stats(self) -> dict[str, Any]:
        """返回仓库运行统计，供 ``/互动状态`` 展示。

        Returns:
            含会话数、用户数、待落盘数的字典。
        """
        users = sum(len(d["users"]) for d in self._cache.values())
        polls = sum(len(d["polls"]) for d in self._cache.values())
        files = len(list(self.data_dir.glob("*.json")))
        return {
            "cached_sessions": len(self._cache),
            "cached_users": users,
            "cached_polls": polls,
            "dirty_sessions": len(self._dirty),
            "disk_files": files,
        }

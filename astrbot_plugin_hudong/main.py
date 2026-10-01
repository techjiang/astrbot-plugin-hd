"""互动 —— 新一代群聊互动 AstrBot 插件。

提供签到、积分、抽奖、投票、猜数字、词语接龙、排行榜、关键词互动等
一整套群聊玩法。数据按会话隔离持久化到 AstrBot 数据目录。

作者：科技酱
网站：https://docs.asoe.cn
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

from .games import ChainGame, GuessGame, lottery_draw, sign_in
from .store import InteractionStore


class InteractionPlugin(Star):
    """互动插件主类。

    负责指令注册、会话上下文管理，以及各玩法的编排调度。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """初始化插件。

        Args:
            context: AstrBot 插件上下文。
            config: 插件配置字典（来自 _conf_schema.json）。
        """
        super().__init__(context, config)
        self.config: dict[str, Any] = config or {}
        data_dir = Path("data") / "interaction"
        self.store = InteractionStore(data_dir)
        self._guesses: dict[str, GuessGame] = {}
        self._chains: dict[str, ChainGame] = {}
        self._votes: dict[str, dict[str, Any]] = {}
        self._cooldown: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """插件加载后启动后台清理任务。"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("[互动] 插件已加载，作者：科技酱 (https://docs.asoe.cn)")

    async def terminate(self) -> None:
        """插件卸载/重载时刷盘并清理后台任务。"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        await self.store.flush_all()
        logger.info("[互动] 插件已卸载，数据已保存。")

    # ------------------------------------------------------------------ 内部工具

    def _cfg(self, *path: str, default: Any = None) -> Any:
        """按路径读取嵌套配置。

        Args:
            *path: 配置键路径，如 ``("sign_in", "enabled")``。
            default: 路径不存在时的默认值。

        Returns:
            读取到的配置值。
        """
        node: Any = self.config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _session_key(self, event: AstrMessageEvent) -> str:
        """生成会话唯一标识，群聊按群隔离，私聊按用户隔离。

        Args:
            event: 消息事件。

        Returns:
            会话标识字符串。
        """
        platform = event.get_platform_name()
        gid = event.get_group_id()
        if gid:
            return f"{platform}_group_{gid}"
        return f"{platform}_private_{event.get_sender_id()}"

    def _guard(self, event: AstrMessageEvent) -> str | None:
        """执行通用前置检查（总开关、群聊限制、冷却）。

        Args:
            event: 消息事件。

        Returns:
            拦截原因；通过检查时返回 ``None``。
        """
        if not self._cfg("enabled", default=True):
            return "互动插件当前已关闭。"
        if (
            self._cfg("permission", "group_only", default=True)
            and not event.get_group_id()
        ):
            return "该玩法仅在群聊中可用哦。"
        cooldown = int(self._cfg("permission", "cooldown_seconds", default=1) or 0)
        if cooldown > 0:
            key = f"{event.get_session_id()}:{event.get_sender_id()}"
            now = time.time()
            if now - self._cooldown.get(key, 0) < cooldown:
                return "操作太快啦，稍等一下～"
            self._cooldown[key] = now
        return None

    def _deny(self, reason: str) -> MessageEventResult:
        """构造一条拒绝提示结果。

        Args:
            reason: 提示文案。

        Returns:
            消息事件结果。
        """
        return MessageEventResult().message(reason)

    async def _cleanup_loop(self) -> None:
        """定时清理过期的游戏与投票状态，并定期落盘。"""
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                guess_ttl = 600
                self._guesses = {
                    k: g
                    for k, g in self._guesses.items()
                    if now - g.started_at < guess_ttl
                }
                chain_ttl = int(
                    self._cfg("word_chain", "timeout_seconds", default=60) or 60
                )
                self._chains = {
                    k: g
                    for k, g in self._chains.items()
                    if now - g.started_at < chain_ttl
                }
                duration = int(
                    self._cfg("vote", "duration_seconds", default=300) or 300
                )
                expired = [
                    pid
                    for pid, v in self._votes.items()
                    if now - float(v.get("started_at", now)) >= duration
                ]
                for pid in expired:
                    self._votes.pop(pid, None)
                await self.store.flush_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 后台任务需吞掉异常以免中断
                logger.error(f"[互动] 清理任务异常: {e}")

    # ------------------------------------------------------------------ 签到/积分

    @filter.command("签到", alias={"sign", "打卡"})
    async def cmd_sign(self, event: AstrMessageEvent) -> None:
        """每日签到，领取互动积分。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._cfg("sign_in", "enabled", default=True):
            yield self._deny("签到功能已关闭。")
            return
        key = self._session_key(event)
        user = self.store.get_user(key, event.get_sender_id())
        ok, _reward, streak, msg = sign_in(
            user,
            int(self._cfg("sign_in", "min_reward", default=10) or 10),
            int(self._cfg("sign_in", "max_reward", default=50) or 50),
            float(self._cfg("sign_in", "streak_bonus", default=0.1) or 0),
            float(self._cfg("sign_in", "max_streak_bonus", default=2.0) or 2.0),
        )
        await self.store.save(key)
        name = event.get_sender_name() or "你"
        unit = self._cfg("currency_name", default="互动币")
        if ok:
            yield event.plain_result(
                f"@{name} {msg}\n当前余额：{user['balance']} {unit}，连签 {streak} 天。"
            )
        else:
            yield event.plain_result(f"@{name} {msg}")

    @filter.command("积分", alias={"余额", "balance"})
    async def cmd_balance(self, event: AstrMessageEvent) -> None:
        """查询自己的积分余额与统计。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        user = self.store.get_user(key, event.get_sender_id())
        unit = self._cfg("currency_name", default="互动币")
        name = event.get_sender_name() or "你"
        yield event.plain_result(
            f"@{name} 你好，\n"
            f"💰 余额：{user['balance']} {unit}\n"
            f"📅 累计签到：{user['total_sign']} 天（连签 {user['streak']}，最长 {user['best_streak']}）\n"
            f"🎰 抽奖次数：{user['lottery_count']}，🎯 猜中：{user['guess_win']}，🔗 接龙：{user['chain_win']}"
        )

    @filter.command("转账", alias={"转积分"})
    async def cmd_transfer(
        self, event: AstrMessageEvent, target: str, amount: int
    ) -> None:
        """把积分转给群里的其他成员。

        Args:
            target: 目标用户（@ 或 ID）。
            amount: 转账数量。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        target = target.lstrip("@")
        if amount <= 0:
            yield event.plain_result("转账数量必须大于 0。")
            return
        key = self._session_key(event)
        sender = event.get_sender_id()
        if target == sender:
            yield event.plain_result("不能给自己转账哦。")
            return
        user = self.store.get_user(key, sender)
        if user["balance"] < amount:
            yield event.plain_result(f"余额不足，当前只有 {user['balance']}。")
            return
        self.store.add_balance(key, sender, -amount)
        self.store.add_balance(key, target, amount)
        await self.store.save(key)
        unit = self._cfg("currency_name", default="互动币")
        yield event.plain_result(f"已转给 @{target} {amount} {unit}。")

    # ------------------------------------------------------------------ 抽奖

    @filter.command("抽奖", alias={"抽卡", "lottery"})
    async def cmd_lottery(self, event: AstrMessageEvent) -> None:
        """消耗积分抽奖。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._cfg("lottery", "enabled", default=True):
            yield self._deny("抽奖功能已关闭。")
            return
        key = self._session_key(event)
        user = self.store.get_user(key, event.get_sender_id())
        ok, delta, prize, err = lottery_draw(
            user,
            int(self._cfg("lottery", "cost", default=20) or 0),
            int(self._cfg("lottery", "cooldown_seconds", default=30) or 0),
        )
        await self.store.save(key)
        name = event.get_sender_name() or "你"
        unit = self._cfg("currency_name", default="互动币")
        if not ok:
            yield event.plain_result(f"@{name} {err}")
            return
        yield event.plain_result(
            f"@{name} 抽奖结果：{prize}\n本次变动 {delta:+d}，余额 {user['balance']} {unit}。"
        )

    # ------------------------------------------------------------------ 猜数字

    @filter.command("猜数字", alias={"猜数"})
    async def cmd_guess_start(self, event: AstrMessageEvent) -> None:
        """开始一局猜数字游戏。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._cfg("guess_number", "enabled", default=True):
            yield self._deny("猜数字功能已关闭。")
            return
        key = self._session_key(event)
        game = GuessGame.new(
            int(self._cfg("guess_number", "min", default=1) or 1),
            int(self._cfg("guess_number", "max", default=100) or 100),
            int(self._cfg("guess_number", "max_attempts", default=10) or 10),
        )
        self._guesses[key] = game
        yield event.plain_result(
            f"猜数字开始！范围 {game.low} ~ {game.high}，"
            f"共 {game.max_attempts} 次机会。\n发送「猜 <数字>」来猜。"
        )

    @filter.command("猜")
    async def cmd_guess(self, event: AstrMessageEvent, number: int) -> None:
        """提交猜数字答案。

        Args:
            number: 猜测的数字。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        game = self._guesses.get(key)
        if not game:
            yield event.plain_result(
                "当前没有进行中的猜数字，发送「猜数字」开始一局吧。"
            )
            return
        if time.time() - game.started_at > 600:
            self._guesses.pop(key, None)
            yield event.plain_result("上一局已超时，发送「猜数字」重新开始。")
            return
        result, hint = game.guess(number)
        if result in ("win", "lose"):
            self._guesses.pop(key, None)
            if result == "win":
                reward = int(self._cfg("guess_number", "reward", default=30) or 0)
                user = self.store.get_user(key, event.get_sender_id())
                user["guess_win"] = int(user.get("guess_win", 0)) + 1
                self.store.add_balance(key, event.get_sender_id(), reward)
                await self.store.save(key, force=True)
                hint += f" 奖励 {reward} 积分！"
        yield event.plain_result(hint)

    # ------------------------------------------------------------------ 词语接龙

    @filter.command("接龙", alias={"词语接龙"})
    async def cmd_chain_start(self, event: AstrMessageEvent) -> None:
        """开始一局词语接龙。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._cfg("word_chain", "enabled", default=True):
            yield self._deny("接龙功能已关闭。")
            return
        key = self._session_key(event)
        args = event.message_str.split(maxsplit=1)
        first = args[1].strip() if len(args) > 1 else "互动"
        game = ChainGame(last_word=first)
        game.used.add(first)
        self._chains[key] = game
        yield event.plain_result(
            f"词语接龙开始！起始词：「{first}」。\n"
            f"请发送以「{first[-1]}」开头的两字以上中文词语，直接发词语即可。"
        )

    @filter.regex(r"^[\u4e00-\u9fff]{2,8}$")
    async def on_chain_message(self, event: AstrMessageEvent) -> None:
        """自动识别群里的接龙词语。

        Args:
            event: 消息事件。
        """
        key = self._session_key(event)
        game = self._chains.get(key)
        if not game or game.last_user == event.get_sender_id():
            return
        timeout = int(self._cfg("word_chain", "timeout_seconds", default=60) or 60)
        if time.time() - game.started_at > timeout:
            self._chains.pop(key, None)
            return
        ok, hint = game.submit(event.message_str.strip(), event.get_sender_id())
        if not ok:
            return
        game.started_at = time.time()
        reward = int(self._cfg("word_chain", "reward", default=5) or 0)
        user = self.store.get_user(key, event.get_sender_id())
        user["chain_win"] = int(user.get("chain_win", 0)) + 1
        self.store.add_balance(key, event.get_sender_id(), reward)
        await self.store.save(key)
        event.stop_event()
        yield event.plain_result(f"{hint}（+{reward} 积分）")

    # ------------------------------------------------------------------ 投票

    @filter.command("投票")
    async def cmd_vote_create(self, event: AstrMessageEvent) -> None:
        """发起一个投票，用法：/投票 问题 | 选项1 | 选项2 ..."""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._cfg("vote", "enabled", default=True):
            yield self._deny("投票功能已关闭。")
            return
        payload = event.message_str.split(maxsplit=1)
        if len(payload) < 2 or "|" not in payload[1]:
            yield event.plain_result(
                "用法：/投票 问题 | 选项1 | 选项2 | ...\n例如：/投票 晚饭吃啥 | 火锅 | 烧烤"
            )
            return
        parts = [p.strip() for p in payload[1].split("|") if p.strip()]
        if len(parts) < 3:
            yield event.plain_result("至少需要 1 个问题和 2 个选项。")
            return
        question, options = parts[0], parts[1:]
        max_options = int(self._cfg("vote", "max_options", default=10) or 10)
        if len(options) > max_options:
            yield event.plain_result(f"选项最多 {max_options} 个。")
            return
        poll_id = uuid.uuid4().hex[:8]
        self._votes[poll_id] = {
            "question": question,
            "options": options,
            "votes": {},
            "started_at": time.time(),
            "owner": event.get_sender_id(),
        }
        duration = int(self._cfg("vote", "duration_seconds", default=300) or 300)
        lines = [f"📊 投票：{question}", f"编号：{poll_id}，时长 {duration // 60} 分钟"]
        for i, opt in enumerate(options, 1):
            lines.append(f"{i}. {opt}")
        lines.append(f"发送「投 <编号> <选项序号>」参与，例如：投 {poll_id} 1")
        yield event.plain_result("\n".join(lines))

    @filter.command("投")
    async def cmd_vote_cast(
        self, event: AstrMessageEvent, poll_id: str, option: int
    ) -> None:
        """为指定投票选项投票。

        Args:
            poll_id: 投票编号。
            option: 选项序号（从 1 开始）。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        poll = self._votes.get(poll_id)
        if not poll:
            yield event.plain_result(f"没有找到编号为 {poll_id} 的投票。")
            return
        if option < 1 or option > len(poll["options"]):
            yield event.plain_result(f"选项序号应为 1 ~ {len(poll['options'])}。")
            return
        uid = event.get_sender_id()
        if uid in poll["votes"]:
            yield event.plain_result("你已经投过票啦，一人一票哦。")
            return
        poll["votes"][uid] = option - 1
        yield event.plain_result(
            f"已投票：{poll['options'][option - 1]}（当前共 {len(poll['votes'])} 票）"
        )

    @filter.command("投票结果", alias={"查看投票"})
    async def cmd_vote_result(self, event: AstrMessageEvent, poll_id: str) -> None:
        """查看投票结果。

        Args:
            poll_id: 投票编号。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        poll = self._votes.get(poll_id)
        if not poll:
            yield event.plain_result(f"没有找到编号为 {poll_id} 的投票。")
            return
        counts = [0] * len(poll["options"])
        for idx in poll["votes"].values():
            counts[idx] += 1
        total = sum(counts) or 1
        lines = [f"📊 {poll['question']}（共 {sum(counts)} 票）"]
        for i, (opt, cnt) in enumerate(zip(poll["options"], counts), 1):
            bar = "█" * int(cnt / total * 20)
            lines.append(f"{i}. {opt} — {cnt} 票 {bar}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 排行榜

    @filter.command("排行榜", alias={"排行", "rank"})
    async def cmd_rank(self, event: AstrMessageEvent, metric: str = "balance") -> None:
        """查看积分/签到等排行榜。

        Args:
            metric: 排序维度，可选 balance/total_sign/lottery_count。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        aliases = {
            "积分": "balance",
            "签到": "total_sign",
            "抽奖": "lottery_count",
            "猜中": "guess_win",
            "接龙": "chain_win",
        }
        metric = aliases.get(metric, metric)
        if metric not in {
            "balance",
            "total_sign",
            "lottery_count",
            "guess_win",
            "chain_win",
        }:
            yield event.plain_result(
                "可排行的维度：积分 / 签到 / 抽奖 / 猜中 / 接龙。如：/排行榜 签到"
            )
            return
        key = self._session_key(event)
        size = int(self._cfg("rank", "size", default=10) or 10)
        rows = self.store.top_users(key, by=metric, limit=size)
        if not rows:
            yield event.plain_result("还没有数据，快去签到或抽奖吧！")
            return
        titles = {
            "balance": "积分",
            "total_sign": "签到",
            "lottery_count": "抽奖",
            "guess_win": "猜中",
            "chain_win": "接龙",
        }
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"🏆 本群{titles[metric]}排行榜"]
        for i, (uid, value) in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{prefix} {uid} — {value}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 关键词互动

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_keyword(self, event: AstrMessageEvent) -> None:
        """命中配置关键词时自动回复。

        Args:
            event: 消息事件。
        """
        if not self._cfg("auto_reply", "enabled", default=False):
            return
        if not self._cfg("enabled", default=True):
            return
        text = event.message_str.strip()
        if not text:
            return
        rules = self._cfg("auto_reply", "rules", default=[]) or []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            keyword = str(rule.get("keyword", "")).strip()
            reply = str(rule.get("reply", "")).strip()
            if not keyword or not reply:
                continue
            exact = bool(rule.get("exact", False))
            if (exact and text == keyword) or (not exact and keyword in text):
                event.stop_event()
                yield event.plain_result(reply)
                return

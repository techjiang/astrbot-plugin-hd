"""互动 —— 新一代群聊互动 AstrBot 插件。

提供签到、积分、转账、抽奖、猜数字、词语接龙、投票、排行榜、
每日任务、打劫、掷骰、关键词互动等一整套群聊玩法，
并附带丰富的 WebUI 可视化配置。

数据按「平台 + 群/私聊」会话隔离，原子落盘、不阻塞事件循环。

作者：科技酱
网站：https://docs.asoe.cn
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

from . import games
from .games import ChainGame, GuessGame
from .store import InteractionStore

# 各指令名与别名。`/指令` 后收到的 message_str 仍包含指令名本身
# （AstrBot 不会裁剪），因此必须按此表剥离前缀后才能拿到真正的参数。
COMMAND_NAMES: tuple[str, ...] = (
    "互动状态",
    "互动帮助",
    "词语接龙",
    "投票结果",
    "每日任务",
    "猜数字",
    "排行榜",
    "查看投票",
    "掷骰子",
    "签到",
    "打卡",
    "积分",
    "余额",
    "我的",
    "转账",
    "转积分",
    "抽奖",
    "抽卡",
    "猜数",
    "猜",
    "接龙",
    "投票",
    "投",
    "领取",
    "领奖",
    "打劫",
    "抢劫",
    "掷骰",
    "骰子",
    "排行",
    "榜单",
    "任务",
    "互动",
)

# 数据根目录：AstrBot 的 data 目录下，插件卸载重装不丢数据
DATA_SUBDIR = Path("data") / "astrbot_plugin_hudong"

# 猜数字/接龙局状态 TTL，超过即视为过期
GUESS_TTL = 900
# 投票超期后保留多久（便于查看结果）
POLL_KEEP = 86400
# 后台清理任务间隔
CLEAN_INTERVAL = 60


class InteractionPlugin(Star):
    """互动插件主类。

    负责指令注册、会话上下文管理，以及各玩法的编排调度。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """初始化插件。

        Args:
            context: AstrBot 插件上下文。
            config: 插件配置（来自 ``_conf_schema.json``）。
                注意：``Star`` 基类不会保存 config，需自行持有。
        """
        try:
            super().__init__(context, config)
        except TypeError:
            # 兼容未接受 config 参数的旧版基类
            super().__init__(context)
        # AstrBot 会把 AstrBotConfig 传进来；转成普通 dict 以便安全 get
        self.config: dict[str, Any] = dict(config) if config else {}
        self.store = InteractionStore(DATA_SUBDIR)
        self._guesses: dict[str, GuessGame] = {}
        self._chains: dict[str, ChainGame] = {}
        self._cooldown: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._rng = random.Random()

    # ------------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        """插件激活后启动后台维护任务。"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("[互动] 插件已加载 · 作者：科技酱 https://docs.asoe.cn")

    async def terminate(self) -> None:
        """插件禁用/重载时刷盘并结束后台任务。"""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._cleanup_task
            self._cleanup_task = None
        if hasattr(self, "store"):
            await self.store.close()
        logger.info("[互动] 插件已卸载，数据已保存。")

    # ------------------------------------------------------------------ 配置与工具

    def _cfg(self, *path: str, default: Any = None) -> Any:
        """按路径读取嵌套配置。

        Args:
            *path: 配置键路径，如 ``("sign_in", "enabled")``。
            default: 路径不存在时的默认值。

        Returns:
            配置值。
        """
        node: Any = self.config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return default if node is None else node

    def _int(self, *path: str, default: int = 0) -> int:
        """读取整数配置，非法值回退默认。

        Args:
            *path: 配置键路径。
            default: 默认值。

        Returns:
            整数配置值。
        """
        try:
            return int(self._cfg(*path, default=default))
        except (TypeError, ValueError):
            return default

    def _float(self, *path: str, default: float = 0.0) -> float:
        """读取浮点配置，非法值回退默认。

        Args:
            *path: 配置键路径。
            default: 默认值。

        Returns:
            浮点配置值。
        """
        try:
            return float(self._cfg(*path, default=default))
        except (TypeError, ValueError):
            return default

    @property
    def _unit(self) -> str:
        """当前积分单位名称。"""
        return str(self._cfg("currency_name", default="互动币") or "互动币")

    def _session_key(self, event: AstrMessageEvent) -> str:
        """生成会话唯一标识：群聊按群隔离，私聊按人隔离。

        Args:
            event: 消息事件。

        Returns:
            会话标识。
        """
        platform = event.get_platform_name() or "unknown"
        gid = event.get_group_id()
        if gid:
            return f"{platform}_group_{gid}"
        return f"{platform}_private_{event.get_sender_id()}"

    def _sender(self, event: AstrMessageEvent) -> tuple[str, str]:
        """返回 ``(用户 ID, 展示昵称)``，并顺带把昵称写进档案。

        Args:
            event: 消息事件。

        Returns:
            ``(用户 ID, 昵称)``。
        """
        uid = str(event.get_sender_id())
        name = (event.get_sender_name() or "").strip() or uid
        return uid, name

    def _guard(self, event: AstrMessageEvent) -> str | None:
        """通用前置检查：总开关、群聊限制、冷却。

        Args:
            event: 消息事件。

        Returns:
            拦截原因；通过时返回 ``None``。
        """
        if not self._cfg("enabled", default=True):
            return "互动插件当前已关闭。"
        if self._cfg("permission", "group_only", default=True) and not (
            event.get_group_id()
        ):
            return "该玩法仅在群聊中可用哦。"

        cooldown = self._int("permission", "cooldown_seconds", default=1)
        if cooldown <= 0:
            return None

        key = f"{self._session_key(event)}:{event.get_sender_id()}"
        now = time.time()
        if now - self._cooldown.get(key, 0.0) < cooldown:
            return "操作太快啦，稍等一下～"
        self._cooldown[key] = now
        # 防止长期运行后冷却表无限膨胀
        if len(self._cooldown) > 4096:
            cutoff = now - max(cooldown * 10, 600)
            self._cooldown = {k: v for k, v in self._cooldown.items() if v >= cutoff}
        return None

    @staticmethod
    def _deny(reason: str) -> MessageEventResult:
        """构造一条拒绝提示。

        Args:
            reason: 提示文案。

        Returns:
            消息结果。
        """
        return MessageEventResult().message(reason)

    @staticmethod
    def _args(event: AstrMessageEvent) -> str:
        """取出「指令名之后」的参数文本。

        AstrBot 在唤醒阶段会剥掉 ``wake_prefix``，但**不会**剥掉指令名本身，
        ``event.message_str`` 里始终带着指令名。因此这里按 ``COMMAND_NAMES``
        剥掉开头的指令名，避免首个参数误吞指令（例如 ``/接龙 互动``
        过去会把起始词解析成「接龙」）。

        Args:
            event: 消息事件。

        Returns:
            指令之后的参数文本。
        """
        text = (event.message_str or "").strip()
        # 最长的名字优先，避免「互动状态」被「互动」抢先匹配
        for name in sorted(COMMAND_NAMES, key=len, reverse=True):
            if text == name:
                return ""
            if text.startswith(name) and text[len(name)] in " \t":
                return text[len(name) :].strip()
        return text

    def _feature_on(self, name: str, default: bool = True) -> bool:
        """判断某个玩法是否启用。

        Args:
            name: 配置分组名。
            default: 缺省值。

        Returns:
            是否启用。
        """
        return bool(self._cfg(name, "enabled", default=default))

    # ------------------------------------------------------------------ 后台维护

    async def _cleanup_loop(self) -> None:
        """定时清理过期局状态、投票与冷却表，并周期性落盘。"""
        while True:
            try:
                await asyncio.sleep(CLEAN_INTERVAL)
                now = time.time()
                self._guesses = {
                    k: g
                    for k, g in self._guesses.items()
                    if now - g.started_at < GUESS_TTL
                }
                chain_ttl = self._int("word_chain", "timeout_seconds", default=60) * 3
                self._chains = {
                    k: g
                    for k, g in self._chains.items()
                    if now - g.started_at < max(180, chain_ttl)
                }
                self.store.prune_polls(POLL_KEEP, now)
                if len(self._cooldown) > 4096:
                    self._cooldown.clear()
                await self.store.flush_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 后台任务必须吞异常
                logger.error(f"[互动] 后台维护任务异常: {e}")

    # ------------------------------------------------------------------ 签到 / 积分

    @filter.command("签到", alias={"sign", "打卡"})
    async def cmd_sign(self, event: AstrMessageEvent) -> None:
        """每日签到领取积分。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("sign_in"):
            yield self._deny("签到功能已关闭。")
            return

        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        ok, reward, streak, msg = games.sign_in(
            user,
            self._int("sign_in", "min_reward", default=10),
            self._int("sign_in", "max_reward", default=50),
            self._float("sign_in", "streak_bonus", default=0.1),
            self._float("sign_in", "max_streak_bonus", default=2.0),
            rng=self._rng,
        )
        if ok:
            games.bump_daily(user, "sign")
        await self.store.save(key)
        flavor = self._rng.choice(games.SIGN_FLAVORS)
        if ok:
            yield event.plain_result(
                f"@{name} {msg}\n当前余额 {user['balance']} {self._unit}，"
                f"已连续签到 {streak} 天。\n{flavor}"
            )
        else:
            yield event.plain_result(f"@{name} {msg}")

    @filter.command("积分", alias={"余额", "balance", "我的"})
    async def cmd_balance(self, event: AstrMessageEvent) -> None:
        """查询个人积分与统计。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        level, inner, need = games.level_of(user)
        bar = "▰" * (inner * 10 // need) + "▱" * (10 - inner * 10 // need)
        title = user.get("title") or "暂无"
        yield event.plain_result(
            f"@{name} 你好，\n"
            f"💰 余额：{user['balance']} {self._unit}\n"
            f"🏅 等级：Lv.{level}  {bar} {inner}/{need}\n"
            f"🎖 称号：{title}\n"
            f"📅 累计签到 {user['total_sign']} 天（连签 {user['streak']}，最长 {user['best_streak']}）\n"
            f"🎰 抽奖 {user['lottery_count']} 次｜🎯 猜中 {user['guess_win']} 次｜"
            f"🔗 接龙 {user['chain_win']} 次｜🥊 打劫 {user['rob_win']} 胜 {user['rob_lose']} 负"
        )

    @filter.command("转账", alias={"转积分", "pay"})
    async def cmd_transfer(
        self, event: AstrMessageEvent, target: str, amount: int
    ) -> None:
        """把积分转给群里其他成员。

        Args:
            target: 目标用户 ID 或 @某人。
            amount: 转账数量。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        uid, _ = self._sender(event)
        target = str(target).lstrip("@").strip()
        if not target:
            yield event.plain_result("请指定转账对象，例如：/转账 10001 50")
            return
        ok, msg = self.store.transfer(key, uid, target, int(amount))
        if not ok:
            yield event.plain_result(msg)
            return
        await self.store.save(key, force=True)
        yield event.plain_result(
            f"已转给 {self.store.display_name(key, target)} {amount} {self._unit}。"
        )

    # ------------------------------------------------------------------ 抽奖

    @filter.command("抽奖", alias={"抽卡", "lottery"})
    async def cmd_lottery(self, event: AstrMessageEvent) -> None:
        """消耗积分抽奖。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("lottery"):
            yield self._deny("抽奖功能已关闭。")
            return

        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        ok, delta, prize, err = games.lottery_draw(
            user,
            self._int("lottery", "cost", default=20),
            self._int("lottery", "cooldown_seconds", default=30),
            rng=self._rng,
        )
        if ok:
            games.bump_daily(user, "lottery")
        await self.store.save(key)
        if not ok:
            yield event.plain_result(f"@{name} {err}")
            return
        yield event.plain_result(
            f"@{name} 抽奖结果：{prize}\n"
            f"本次净变动 {delta:+d}，余额 {user['balance']} {self._unit}。"
        )

    # ------------------------------------------------------------------ 猜数字

    @filter.command("猜数字", alias={"猜数", "guess"})
    async def cmd_guess_start(self, event: AstrMessageEvent) -> None:
        """开始一局猜数字。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("guess_number"):
            yield self._deny("猜数字功能已关闭。")
            return

        key = self._session_key(event)
        game = GuessGame.new(
            self._int("guess_number", "min", default=1),
            self._int("guess_number", "max", default=100),
            self._int("guess_number", "max_attempts", default=10),
            rng=self._rng,
        )
        self._guesses[key] = game
        yield event.plain_result(
            f"🎯 猜数字开始！范围 {game.low} ~ {game.high}，"
            f"共 {game.max_attempts} 次机会。\n发送「猜 <数字>」来猜，"
            f"猜中得 {self._int('guess_number', 'reward', default=30)} {self._unit}。"
        )

    @filter.command("猜", alias={"cai"})
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
        if game is None:
            yield event.plain_result(
                "当前没有进行中的猜数字，发送「猜数字」开始一局吧。"
            )
            return
        if time.time() - game.started_at > GUESS_TTL:
            self._guesses.pop(key, None)
            yield event.plain_result("上一局已超时，发送「猜数字」重新开始。")
            return

        uid, name = self._sender(event)
        result, hint = game.guess(int(number))
        if result in ("win", "lose"):
            self._guesses.pop(key, None)
            if result == "win":
                reward = self._int("guess_number", "reward", default=30)
                user = self.store.get_user(key, uid)
                user["name"] = name
                user["guess_win"] = int(user.get("guess_win", 0) or 0) + 1
                user["balance"] = int(user.get("balance", 0) or 0) + reward
                games.bump_daily(user, "guess")
                await self.store.save(key, force=True)
                hint += f" 奖励 {reward} {self._unit}！"
        yield event.plain_result(f"@{name} {hint}")

    # ------------------------------------------------------------------ 词语接龙

    @filter.command("接龙", alias={"词语接龙", "chain"})
    async def cmd_chain_start(self, event: AstrMessageEvent) -> None:
        """开始一局词语接龙，可带起始词。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("word_chain"):
            yield self._deny("接龙功能已关闭。")
            return

        key = self._session_key(event)
        # 修正常见错误：event.message_str 含指令名，必须剥掉后再取起始词
        arg = self._args(event).strip()
        first = arg.split()[0] if arg else ""
        if not first or not all("\u4e00" <= ch <= "\u9fff" for ch in first):
            first = self._rng.choice(
                ("互动", "科技", "群聊", "开心", "生活", "音乐", "星空")
            )
        game = ChainGame(last_word=first)
        game.used.add(first)
        self._chains[key] = game
        yield event.plain_result(
            f"🔗 词语接龙开始！起始词：「{first}」\n"
            f"请发送以「{first[-1]}」开头的两字以上中文词语，直接发词语即可，"
            f"{self._int('word_chain', 'timeout_seconds', default=60)} 秒内接不上就结束。"
        )

    @filter.regex(r"^[\u4e00-\u9fff]{2,12}$")
    async def on_chain_message(self, event: AstrMessageEvent) -> None:
        """自动识别群里符合规则的接龙词语。

        Args:
            event: 消息事件。
        """
        if not self._cfg("enabled", default=True):
            return
        if not self._feature_on("word_chain"):
            return

        key = self._session_key(event)
        game = self._chains.get(key)
        if game is None:
            return
        timeout = self._int("word_chain", "timeout_seconds", default=60)
        if time.time() - game.started_at > timeout:
            self._chains.pop(key, None)
            return

        uid, name = self._sender(event)
        word = (event.message_str or "").strip()
        ok, hint = game.submit(word, uid)
        if not ok:
            # 只有"轮到自己"的玩家才需要收到失败提示
            if game.last_user and uid != game.last_user:
                event.stop_event()
                yield event.plain_result(hint)
            return

        game.started_at = time.time()
        reward = self._int("word_chain", "reward", default=5)
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["chain_win"] = int(user.get("chain_win", 0) or 0) + 1
        user["balance"] = int(user.get("balance", 0) or 0) + reward
        games.bump_daily(user, "chain")
        await self.store.save(key)
        event.stop_event()
        yield event.plain_result(f"@{name} {hint}（+{reward} {self._unit}）")

    # ------------------------------------------------------------------ 投票

    @filter.command("投票")
    async def cmd_vote_create(self, event: AstrMessageEvent) -> None:
        """发起投票，用法：/投票 问题 | 选项1 | 选项2 ..."""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("vote"):
            yield self._deny("投票功能已关闭。")
            return

        # 修正常见错误：无参数时旧代码 IndexError
        raw = self._args(event).strip()
        parts = [p.strip() for p in raw.replace("｜", "|").split("|") if p.strip()]
        if len(parts) < 3:
            yield event.plain_result(
                "用法：/投票 问题 | 选项1 | 选项2\n例如：/投票 晚饭吃啥 | 火锅 | 烧烤"
            )
            return

        question, options = parts[0], parts[1:]
        max_options = self._int("vote", "max_options", default=10)
        if len(options) > max_options:
            yield event.plain_result(
                f"选项最多 {max_options} 个，当前 {len(options)} 个。"
            )
            return

        key = self._session_key(event)
        uid, _ = self._sender(event)
        duration = self._int("vote", "duration_seconds", default=300)
        poll_id = uuid.uuid4().hex[:6]
        poll = {
            "id": poll_id,
            "question": question[:100],
            "options": [o[:50] for o in options],
            "votes": {},
            "owner": uid,
            "started_at": time.time(),
            "duration": duration,
        }
        self.store.add_poll(key, poll)
        await self.store.save(key, force=True)

        lines = [f"📊 投票：{question}", f"编号 {poll_id}｜时长 {duration // 60} 分钟"]
        lines += [f"{i}. {opt}" for i, opt in enumerate(poll["options"], 1)]
        lines.append(f"参与方式：/投 {poll_id} <选项序号>")
        yield event.plain_result("\n".join(lines))

    @filter.command("投")
    async def cmd_vote_cast(
        self, event: AstrMessageEvent, poll_id: str, option: int
    ) -> None:
        """为指定投票选项投票。

        Args:
            poll_id: 投票编号。
            option: 选项序号，从 1 开始。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        poll = self.store.polls(key).get(str(poll_id).lstrip("#"))
        if not poll:
            yield event.plain_result(f"没有找到编号为 {poll_id} 的投票。")
            return
        options = poll.get("options") or []
        if not (1 <= int(option) <= len(options)):
            yield event.plain_result(f"选项序号应为 1 ~ {len(options)}。")
            return
        uid, _ = self._sender(event)
        votes = poll.setdefault("votes", {})
        if uid in votes:
            yield event.plain_result("你已经投过票啦，一人一票哦。")
            return
        votes[uid] = int(option) - 1
        await self.store.save(key, force=True)
        yield event.plain_result(
            f"✅ 已投票：{options[int(option) - 1]}（当前共 {len(votes)} 票）"
        )

    @filter.command("投票结果", alias={"查看投票"})
    async def cmd_vote_result(self, event: AstrMessageEvent, poll_id: str = "") -> None:
        """查看投票结果；不带编号时列出本会话全部投票。

        Args:
            poll_id: 投票编号，可为空。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        polls = self.store.polls(key)
        if not poll_id:
            if not polls:
                yield event.plain_result(
                    "本群还没有投票，发送「/投票 问题 | 选项 | 选项」发起一个。"
                )
                return
            lines = ["📋 本群投票列表"]
            for pid, poll in list(polls.items())[-10:]:
                lines.append(
                    f"· {pid}｜{poll.get('question', '')}｜{len(poll.get('votes', {}))} 票"
                )
            lines.append("查看详情：/投票结果 <编号>")
            yield event.plain_result("\n".join(lines))
            return

        poll = polls.get(str(poll_id).lstrip("#"))
        if not poll:
            yield event.plain_result(f"没有找到编号为 {poll_id} 的投票。")
            return
        options = poll.get("options") or []
        counts = [0] * len(options)
        for idx in (poll.get("votes") or {}).values():
            if isinstance(idx, int) and 0 <= idx < len(counts):
                counts[idx] += 1
        total = sum(counts)
        lines = [f"📊 {poll.get('question', '')}（共 {total} 票）"]
        for i, (opt, cnt) in enumerate(zip(options, counts, strict=False), 1):
            pct = (cnt / total * 100) if total else 0
            bar = "█" * int(pct / 5)
            lines.append(f"{i}. {opt} — {cnt} 票 {pct:.1f}% {bar}")
        if total:
            winner = options[counts.index(max(counts))]
            lines.append(f"🏆 当前领先：{winner}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 排行榜

    @filter.command("排行榜", alias={"排行", "rank", "榜单"})
    async def cmd_rank(self, event: AstrMessageEvent, metric: str = "积分") -> None:
        """查看本群排行榜。

        Args:
            metric: 排行维度：积分 / 签到 / 抽奖 / 猜中 / 接龙 / 打劫。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        aliases = {
            "积分": "balance",
            "余额": "balance",
            "balance": "balance",
            "签到": "total_sign",
            "sign": "total_sign",
            "抽奖": "lottery_count",
            "lottery": "lottery_count",
            "猜中": "guess_win",
            "guess": "guess_win",
            "接龙": "chain_win",
            "chain": "chain_win",
            "打劫": "rob_win",
            "rob": "rob_win",
        }
        titles = {
            "balance": "积分",
            "total_sign": "签到",
            "lottery_count": "抽奖",
            "guess_win": "猜中",
            "chain_win": "接龙",
            "rob_win": "打劫",
        }
        field = aliases.get(str(metric).strip())
        if field is None:
            yield event.plain_result(
                "可排行维度：积分 / 签到 / 抽奖 / 猜中 / 接龙 / 打劫。例如：/排行榜 签到"
            )
            return

        key = self._session_key(event)
        size = max(3, min(50, self._int("rank", "size", default=10)))
        rows = self.store.top_users(key, by=field, limit=size)
        if not rows:
            yield event.plain_result(f"本群还没有{titles[field]}数据，快去玩一局吧！")
            return

        medals = ("🥇", "🥈", "🥉")
        lines = [f"🏆 本群{titles[field]}排行榜（Top {len(rows)}）"]
        for i, (uid, value) in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i + 1:>2}."
            unit = f" {self._unit}" if field == "balance" else ""
            lines.append(
                f"{prefix} {self.store.display_name(key, uid)} — {value}{unit}"
            )
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 每日任务

    @filter.command("每日任务", alias={"任务", "quest"})
    async def cmd_quest(self, event: AstrMessageEvent) -> None:
        """查看每日任务进度。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        progress = games.quest_progress(user)
        lines = [f"📜 @{name} 的每日任务"]
        for quest, done_count, claimed in progress:
            target = quest["target"]
            mark = "✅" if claimed else ("🎁" if done_count >= target else "⏳")
            lines.append(
                f"{mark} {quest['desc']}（{done_count}/{target}）→ {quest['reward']} {self._unit}"
            )
        lines.append("完成任意任务后发送「/领取 <任务名>」领取奖励。")
        lines.append("任务名：" + " / ".join(q[0]["code"] for q in progress))
        await self.store.save(key)
        yield event.plain_result("\n".join(lines))

    @filter.command("领取", alias={"领奖", "claim"})
    async def cmd_claim(self, event: AstrMessageEvent) -> None:
        """领取每日任务奖励。"""
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        code = self._args(event).strip().split()[0] if self._args(event).strip() else ""

        if not code:
            # 不带参数时自动领取所有已完成任务
            claimed, total_reward, notes = 0, 0, []
            for quest, done_count, is_done in games.quest_progress(user):
                if is_done or done_count < quest["target"]:
                    continue
                ok, reward, msg = games.claim_quest(user, quest["code"])
                if ok:
                    claimed += 1
                    total_reward += reward
                    notes.append(msg)
            if not claimed:
                yield event.plain_result(
                    "暂时没有可领取的任务奖励，发送「每日任务」查看进度。"
                )
                return
            await self.store.save(key, force=True)
            yield event.plain_result(
                f"@{name} 一键领取 {claimed} 个任务，共获得 {total_reward} {self._unit}！\n"
                + "\n".join(notes)
            )
            return

        ok, reward, msg = games.claim_quest(user, code)
        if ok:
            await self.store.save(key, force=True)
        yield event.plain_result(f"@{name} {msg}")

    # ------------------------------------------------------------------ 打劫 / 掷骰

    @filter.command("打劫", alias={"抢劫", "rob"})
    async def cmd_rob(self, event: AstrMessageEvent, target: str, amount: int) -> None:
        """打劫群友的积分。

        Args:
            target: 目标用户 ID。
            amount: 打劫数量。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("rob", default=False):
            yield self._deny("打劫功能已关闭。")
            return

        key = self._session_key(event)
        uid, name = self._sender(event)
        target = str(target).lstrip("@").strip()
        if target == uid:
            yield event.plain_result("不能打劫自己哦。")
            return
        victim = self.store.peek_user(key, target)
        if victim is None:
            yield event.plain_result("对方在本群还没有档案，无法打劫。")
            return

        attacker = self.store.get_user(key, uid)
        attacker["name"] = name
        ok, delta, msg = games.rob_check(attacker, victim, int(amount), rng=self._rng)
        if ok:
            games.bump_daily(attacker, "rob")
        await self.store.save(key, force=True)
        victim_name = self.store.display_name(key, target)
        yield event.plain_result(f"@{name} {msg}\n目标：{victim_name}（{delta:+d}）")

    @filter.command("掷骰", alias={"骰子", "dice", "roll"})
    async def cmd_dice(
        self, event: AstrMessageEvent, count: int = 1, faces: int = 6
    ) -> None:
        """掷骰子。

        Args:
            count: 骰子数量，1~10。
            faces: 面数，2~1000。
        """
        if reason := self._guard(event):
            yield self._deny(reason)
            return
        if not self._feature_on("dice", default=False):
            yield self._deny("掷骰子功能已关闭。")
            return

        key = self._session_key(event)
        uid, name = self._sender(event)
        rolls, total = games.roll_dice(count, faces, rng=self._rng)
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["dice_count"] = int(user.get("dice_count", 0) or 0) + 1
        games.bump_daily(user, "dice")
        await self.store.save(key)
        detail = " + ".join(str(r) for r in rolls)
        yield event.plain_result(f"🎲 @{name} {count}d{faces}：{detail} = {total}")

    # ------------------------------------------------------------------ 帮助 / 状态

    @filter.command("互动", alias={"互动帮助", "hd", "help"})
    async def cmd_help(self, event: AstrMessageEvent) -> None:
        """查看互动插件帮助。"""
        metric = "积分 / 签到 / 抽奖 / 猜中 / 接龙 / 打劫"
        yield event.plain_result(
            "🎮 互动插件 · 玩法总览\n"
            "【日常】\n"
            "/签到 ｜ /积分 ｜ /每日任务 ｜ /领取\n"
            "【娱乐】\n"
            "/抽奖 ｜ /猜数字 → /猜 <数字> ｜ /接龙 [起始词]\n"
            "【社交换】\n"
            f"/转账 <用户> <数量> ｜ /打劫 <用户> <数量>\n"
            "【工具】\n"
            "/投票 问题 | 选项1 | 选项2 → /投 <编号> <序号> → /投票结果 [编号]\n"
            "/掷骰 [数量] [面数] ｜ /排行榜 <维度>\n"
            f"排行维度：{metric}\n"
            f"当前积分单位：{self._unit}"
        )

    @filter.command("互动状态", alias={"hd状态"})
    async def cmd_status(self, event: AstrMessageEvent) -> None:
        """查看插件运行状态（管理员）。"""
        if not event.is_admin():
            yield event.plain_result("只有管理员可以查看运行状态。")
            return
        stats = self.store.stats()
        yield event.plain_result(
            "⚙️ 互动插件运行状态\n"
            f"缓存会话 {stats['cached_sessions']}｜缓存用户 {stats['cached_users']}\n"
            f"缓存投票 {stats['cached_polls']}｜待落盘 {stats['dirty_sessions']}\n"
            f"磁盘文件 {stats['disk_files']}｜猜数字局 {len(self._guesses)}｜接龙局 {len(self._chains)}\n"
            f"数据目录：{self.store.data_dir}"
        )

    # ------------------------------------------------------------------ 关键词互动

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_keyword(self, event: AstrMessageEvent) -> None:
        """命中配置的关键词时自动回复。

        Args:
            event: 消息事件。
        """
        if not self._cfg("enabled", default=True):
            return
        if not self._feature_on("auto_reply", default=False):
            return
        text = (event.message_str or "").strip()
        if not text:
            return

        rules = self._cfg("auto_reply", "rules", default=[]) or []
        if not isinstance(rules, list):
            return
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

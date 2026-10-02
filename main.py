"""互动 —— 新一代群聊互动 AstrBot 插件。

提供签到、积分、转账、抽奖、猜数字、词语接龙、投票、排行榜、每日任务、
打劫、掷骰、幸运数字、魔法八球、扎心，以及 21 点、数字炸弹、猜谜、海龟汤、
抢答、每日运势、称号商店、亲密度、送礼、PK 对决等一整套群聊玩法，
并附带丰富的 WebUI 可视化配置。

**免 @ 触发**：所有指令在群里直接发送即可（``签到``、``/签到``、``!签到``
都能命中），无需 @机器人；游戏开局后直接发消息即可参与，不用任何前缀。

数据按「平台 + 群/私聊」会话隔离，原子落盘、不阻塞事件循环。

作者：科技酱
网站：https://docs.asoe.cn
仓库：https://cnb.cool/asoe/TechSauce/astrbot-plugin-hd
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import random
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

from . import games, games_extra
from .games import ChainGame, GuessGame
from .store import InteractionStore

# 各指令名与别名。
# 重要：AstrBot 在唤醒阶段只剥掉 wake_prefix，**不会**剥掉指令名本身，
# 因此 ``event.message_str`` 里始终带着指令名，必须按此表剥离前缀。
# 新增带参数指令时，务必把指令名与别名都加进来。
COMMAND_NAMES: tuple[str, ...] = (
    "互动状态",
    "互动帮助",
    "词语接龙",
    "每日任务",
    "投票结果",
    "查看投票",
    "幸运数字",
    "魔法八球",
    "猜数字",
    "排行榜",
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
    "接龙",
    "投票",
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
    "八球",
    "扎心",
    "猜",
    "投",
    # ---- v1.1.0 新增指令与别名 ----
    "数字炸弹",
    "海龟汤",
    "每日运势",
    "亲密度",
    "好感度",
    "互动统计",
    "互动导出",
    "互动重置",
    "天气预报",
    "21点",
    "黑杰克",
    "要牌",
    "停牌",
    "猜谜",
    "谜底",
    "谁最先",
    "抢答",
    "占卜",
    "运势",
    "算命",
    "商店",
    "称号",
    "购买",
    "兑换",
    "背包",
    "佩戴",
    "装备",
    "赠送",
    "送礼",
    "表白",
    "对决",
    "决斗",
    "随机",
    "笑话",
    "冷笑话",
    "数字",
    "炸弹",
    "汤底",
    "答案",
    "公布答案",
    "出题",
    "谜语",
    "海龟",
    "手速",
    "黑杰",
    "帮手",
    "菜单",
    "pk",
    "PK",
    "cp",
    "bj",
    "21",
    "hd",
    "roll",
    "dice",
    "rob",
    "pay",
    "gift",
    "buy",
    "shop",
    "bag",
    "wear",
    "joke",
    "random",
    "rush",
    "bomb",
    "turtle",
    "riddle",
    "fortune",
    "confess",
    "duel",
    "intimacy",
    "blackjack",
    "hit",
    "stand",
    "soupanswer",
    "answer",
    "lucky",
    "roast",
    "sign",
    "balance",
    "money",
    "quest",
    "claim",
    "lottery",
    "guess",
    "chain",
    "vote",
    "rank",
    "玩法",
    "状态",
)

# 插件标识，同时用作 ``data/plugin_data/<插件名>`` 下的数据子目录名。
# 必须与 ``metadata.yaml`` 的 ``name`` 保持一致。
PLUGIN_NAME = "astrbot_plugin_hudong"


def resolve_data_dir() -> Path:
    """解析插件的运行时数据目录，固定为 ``data/plugin_data/<plugin_name>``。

    AstrBot 上架规范要求插件把持久化数据放在 ``data/plugin_data/<插件名>`` 下，
    以便插件升级时随 ``plugin_data`` 一起迁移、并便于审计。因此这里**不能**
    直接拼 ``get_astrbot_data_path()``（那只到 ``data`` 这一层）。

    优先调用官方接口 ``get_astrbot_plugin_data_path()``（它返回
    ``<astrbot_root>/data/plugin_data``，且已正确处理 ``ASTRBOT_ROOT``），
    再拼上插件名；接口不可用时（老版本 AstrBot 或脱离框架运行）回退到
    同构的相对路径 ``data/plugin_data/<插件名>`` —— 两条分支落点一致且都合规。

    Returns:
        形如 ``<astrbot_root>/data/plugin_data/astrbot_plugin_hudong`` 的路径。
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        root = Path(get_astrbot_plugin_data_path())
    except Exception:
        # 老版本 AstrBot 或脱离框架运行时：回退到同构的相对路径
        root = Path("data") / "plugin_data"
    return root / PLUGIN_NAME


# 猜数字/接龙局状态 TTL，超过即视为过期
GUESS_TTL = 900
# 投票超期后保留多久（便于查看结果）
POLL_KEEP = 86400
# 后台清理任务间隔
CLEAN_INTERVAL = 60
# 冷却表硬上限，超过则按时间清理
_COOLDOWN_MAX = 4096
# 关键词自动回复的规则条数上限，防止配置被填成巨型列表后每消息全量扫描
_MAX_KEYWORD_RULES = 200
# 接龙需要「轮到别人」时的提示节流窗口（秒），避免刷屏
_CHAIN_HINT_COOLDOWN = 5.0


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
        self.store = InteractionStore(resolve_data_dir())
        self._guesses: dict[str, GuessGame] = {}
        self._chains: dict[str, ChainGame] = {}
        self._cooldown: dict[str, float] = {}
        self._chain_hint_at: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._rng = random.Random()
        # 关键词规则缓存：(规则版本号, 编译结果)
        self._keyword_cache: tuple[int, list[tuple[str, str, bool]]] | None = None
        self._keyword_version = 0
        self._last_rules_signature: str | None = None
        # 免唤醒分发表：归一化指令名 -> 处理协程
        self._dispatch: dict[str, Any] = {}
        # 扩展玩法状态（按会话隔离）
        self._blackjack: dict[str, games_extra.BlackjackGame] = {}
        self._bombs: dict[str, games_extra.BombGame] = {}
        self._riddles: dict[str, dict[str, Any]] = {}
        self._soups: dict[str, dict[str, Any]] = {}
        self._rushes: dict[str, games_extra.RushGame] = {}
        self._build_dispatch()

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

    def _bool(self, *path: str, default: bool = True) -> bool:
        """读取布尔配置，兼容字符串形式的 ``"false"`` / ``"0"``。

        Args:
            *path: 配置键路径。
            default: 默认值。

        Returns:
            布尔配置值。
        """
        value = self._cfg(*path, default=default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off", ""}
        return bool(value)

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
        """返回 ``(用户 ID, 展示昵称)``。

        Args:
            event: 消息事件。

        Returns:
            ``(用户 ID, 昵称)``。
        """
        uid = str(event.get_sender_id())
        name = (event.get_sender_name() or "").strip() or uid
        return uid, name

    @staticmethod
    def _resolve_uid(raw: str) -> str:
        """把「@某人」/``[At:123]``/``123`` 等写法解析成纯用户 ID。

        Args:
            raw: 原始参数。

        Returns:
            纯数字用户 ID；无法解析时返回空字符串。
        """
        text = (raw or "").strip()
        if not text:
            return ""
        for token in (
            text.lstrip("@"),
            text.replace("[At:", "").replace("]", ""),
            text,
        ):
            token = token.strip()
            if token.isdigit():
                return token
        return "".join(ch for ch in text if ch.isdigit())

    def _guard(self, event: AstrMessageEvent) -> str | None:
        """通用前置检查：总开关、群聊限制、冷却。

        Args:
            event: 消息事件。

        Returns:
            拦截原因；通过时返回 ``None``。
        """
        if not self._bool("enabled", default=True):
            return "互动插件当前已关闭。"
        if self._bool("permission", "group_only", default=True) and not (
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
        if len(self._cooldown) > _COOLDOWN_MAX:
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
        # 先剥掉可能的唤醒前缀（/ ! # 等），否则「/骰子 2 6」会匹配不到指令名
        text = text.lstrip("/!#！＃／").strip()
        # 最长的名字优先，避免「互动状态」被「互动」抢先匹配
        for name in sorted(COMMAND_NAMES, key=len, reverse=True):
            if text == name:
                return ""
            if text.startswith(name) and text[len(name)] in " \t\u3000":
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
        return self._bool(name, "enabled", default=default)

    def _dice_params(self, raw: str) -> tuple[int, int, str]:
        """解析掷骰参数，支持 ``3 20`` 与 ``3d20`` 两种写法。

        框架在参数不是合法整数时会把 ``parsed_params`` 丢掉，
        handler 只会拿到默认值，因此这里必须回退到原始文本再解析一遍，
        否则 ``/掷骰 abc`` 会静默变成 ``1d6``。

        Args:
            raw: 指令之后的参数文本。

        Returns:
            ``(数量, 面数, 提示)``，提示为空表示参数正常。
        """
        note = ""
        text = (raw or "").strip().lower().replace("d", " d ").split()
        parsed: list[int] = []
        for token in text[:2]:
            try:
                parsed.append(int(token))
            except (TypeError, ValueError):
                note = "参数不是数字，已按 1d6 处理。"
                return 1, 6, note
        c = parsed[0] if parsed else 1
        f = parsed[1] if len(parsed) > 1 else 6
        c = min(max(1, c), 10)
        f = min(max(2, f), 1000)
        return c, f, note

    def _guess_reward(self) -> int:
        """猜数字奖励：随难度自动缩放，避免「范围开到 100 万仍送 30」的失衡。"""
        lo = self._int("guess_number", "min", default=1)
        hi = self._int("guess_number", "max", default=100)
        lo, hi = games.normalize_range(lo, hi, min_span=1)
        attempts = max(1, self._int("guess_number", "max_attempts", default=10))
        base = self._int("guess_number", "reward", default=30)
        # 用「二分查找的最坏次数」衡量难度，奖励按比例放大
        optimal = max(1, math.ceil(math.log2(hi - lo + 1)))
        scale = max(1.0, attempts / optimal)
        return max(1, int(base * min(scale, 10.0)))

    # ------------------------------------------------------------------ 免唤醒分发

    def _build_dispatch(self) -> None:
        """构建「免唤醒指令表」。

        AstrBot 的标准指令（``@filter.command``）必须经过唤醒阶段，会被
        ``is_at_or_wake_command`` 拦住 —— 也就是说群里不 @机器人 就不会响应。
        为满足「直接发指令即可触发」的需求，这里维护一份
        ``归一化指令名 -> 处理协程`` 的映射，由 ``on_message`` 统一调度。

        每条指令都会被注册为「主名 + 全部别名 + 全角/半角前缀变体」，
        并且优先命中更长的名字（例如「投票结果」先于「投票」）。
        """
        table: dict[str, Any] = {}

        def reg(handler: Any, *names: str) -> None:
            """把若干触发词绑定到同一个处理协程。"""
            for name in names:
                table[name] = handler

        # 日常
        reg(self.cmd_sign, "签到", "打卡", "sign", "qiandao")
        reg(self.cmd_balance, "积分", "余额", "我的", "balance", "money")
        reg(self.cmd_transfer, "转账", "转积分", "pay", "transfer")
        reg(self.cmd_quest, "每日任务", "任务", "quest")
        reg(self.cmd_claim, "领取", "领奖", "claim")

        # 娱乐
        reg(self.cmd_lottery, "抽奖", "抽卡", "lottery")
        reg(self.cmd_guess_start, "猜数字", "猜数", "guess")
        reg(self.cmd_chain_start, "接龙", "词语接龙", "成语接龙", "chain")
        reg(self.cmd_dice, "掷骰", "骰子", "dice", "roll")
        reg(self.cmd_lucky, "幸运数字", "幸运", "lucky")
        reg(self.cmd_eight_ball, "八球", "魔法八球", "8ball")
        reg(self.cmd_roast, "扎心", "扎心话", "roast")
        reg(self.cmd_rob, "打劫", "抢劫", "rob")

        # 扩展玩法
        reg(self.cmd_blackjack, "21点", "黑杰克", "blackjack", "bj")
        reg(self.cmd_bj_hit, "要牌", "hit", "再来一张")
        reg(self.cmd_bj_stand, "停牌", "stand", "不玩了")
        reg(self.cmd_bomb_start, "数字炸弹", "炸弹", "bomb")
        reg(self.cmd_riddle, "猜谜", "谜语", "出题", "riddle")
        reg(self.cmd_riddle_answer, "谜底", "答案", "公布答案")
        reg(self.cmd_soup, "海龟汤", "turtle", "汤")
        reg(self.cmd_soup_answer, "汤底", "soupanswer")
        reg(self.cmd_rush, "谁最先", "抢答", "rush", "手速")
        reg(self.cmd_fortune, "每日运势", "运势", "占卜", "fortune", "算命")
        reg(self.cmd_shop, "称号", "商店", "shop", "道具店")
        reg(self.cmd_buy, "购买", "buy", "兑换")
        reg(self.cmd_bag, "背包", "道具", "bag", "inventory")
        reg(self.cmd_wear, "佩戴", "wear", "装备")

        # 社交
        reg(self.cmd_gift, "赠送", "送礼", "gift")
        reg(self.cmd_intimacy, "亲密度", "好感度", "intimacy", "cp")
        reg(self.cmd_confess, "表白", "confess")
        reg(self.cmd_duel, "pk", "PK", "对决", "决斗", "duel")

        # 工具
        reg(self.cmd_random, "随机", "random", "帮我选")
        reg(self.cmd_joke, "笑话", "joke", "冷笑话")

        # 投票
        reg(self.cmd_vote_create, "投票", "vote")
        reg(self.cmd_vote_result, "投票结果", "查看投票", "票数", "voteresult")

        # 排行与帮助
        reg(self.cmd_rank, "排行榜", "排行", "rank", "榜单")
        reg(self.cmd_help, "互动", "互动帮助", "hd", "help", "菜单", "玩法")
        reg(self.cmd_status, "互动状态", "hd状态", "状态", "status")

        self._dispatch = table

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化消息文本，便于免唤醒匹配。

        去掉首尾空白，剥离开头的唤醒前缀（``/`` ``/`` ``!`` ``！`` ``#`` ``＃``），
        并把连续空白压成一个空格。这样 ``签到``、``/签到``、``!签到``
        都会归一到同一个键。

        Args:
            text: 原始消息文本。

        Returns:
            归一化后的文本（不含前缀）。
        """
        cleaned = (text or "").strip().lstrip("/!#！＃／").strip()
        return " ".join(cleaned.split())

    def _resolve_command(self, text: str) -> tuple[Any, str] | None:
        """把归一化文本解析为 ``(处理协程, 参数串)``。

        采用「最长触发词优先」匹配，避免「投票结果」被「投票」抢先命中。

        Args:
            text: 已归一化的消息文本。

        Returns:
            命中时返回 ``(handler, args)``；未命中返回 ``None``。
        """
        if not text:
            return None
        best: tuple[Any, str] | None = None
        best_len = -1
        for name, handler in self._dispatch.items():
            if len(name) <= best_len:
                continue
            if text == name:
                best, best_len = (handler, ""), len(name)
            elif text.startswith(name) and text[len(name)] in " \t\u3000":
                best, best_len = (handler, text[len(name) :].strip()), len(name)
        return best

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """免唤醒总入口：直接发指令即可触发。

        处理顺序：

        1. 归一化文本，剥离 ``/`` ``!`` ``#`` 等前缀；
        2. 命中已知指令 -> 分发执行（自带通用前置检查）；
        3. 命中关键词规则 -> 自动回复；
        4. 进行中的游戏（猜数字/炸弹/投票/谜语/抢答/接龙）自动接管消息。

        当 ``trigger.wake_free`` 关闭时只做后两步，行为与老版本兼容。

        Args:
            event: 消息事件。
        """
        if not self._bool("enabled", default=True):
            return

        raw = event.message_str or ""
        text = self._normalize(raw)
        if not text:
            return

        if self._bool("trigger", "wake_free", default=True):
            hit = self._resolve_command(text)
            if hit is not None:
                handler, _rest = hit
                # 统一让 handler 用 _args() 自行剥离指令名：那边有完整的
                # COMMAND_NAMES 表，比这里按单个触发词剥离更可靠。
                await self._run_command(handler, "", event)
                return

        # 关键词互动
        if self._feature_on("auto_reply", default=False):
            for keyword, reply, exact in self._keyword_rules():
                if (exact and text == keyword) or (not exact and keyword in text):
                    event.stop_event()
                    await event.send(event.plain_result(reply))
                    return

        # 进行中的游戏自动接管
        if self._bool(
            "trigger", "auto_regex_games", default=True
        ) and await self._auto_game(event, text):
            return

    async def _run_command(
        self, handler: Any, args: str, event: AstrMessageEvent
    ) -> None:
        """执行免唤醒命中的指令，并统一处理前置检查与结果输出。

        Args:
            handler: 指令处理协程。
            args: 指令之后的参数文本。
            event: 消息事件。
        """
        reason = self._guard(event)
        if reason is not None:
            # 冷却期内的刷屏静默丢弃；其余情况给出明确提示
            if not reason.startswith("操作太快"):
                event.stop_event()
                await event.send(self._deny(reason))
            return
        event.stop_event()
        try:
            result = await handler(event, args)
        except Exception as e:  # noqa: BLE001 - 单条指令异常不应打断整条链路
            logger.error(
                f"[互动] 指令 {getattr(handler, '__name__', handler)} 执行失败: {e}"
            )
            return
        if result is not None:
            await event.send(result)

    async def _auto_game(self, event: AstrMessageEvent, text: str) -> bool:
        """接管进行中游戏的无前缀消息。

        Args:
            event: 消息事件。
            text: 归一化文本。

        Returns:
            是否已被某个游戏接管。
        """
        key = self._session_key(event)

        # 猜数字 / 数字炸弹：纯数字
        if text.isdigit():
            if key in self._guesses:
                result = await self._guess_once(event, int(text))
                if result is not None:
                    event.stop_event()
                    await event.send(result)
                    return True
            if key in self._bombs:
                result = await self._bomb_report(event, int(text))
                if result is not None:
                    event.stop_event()
                    await event.send(result)
                    return True

        # 投票：投 <编号> <序号>
        parts = text.split()
        if len(parts) == 3 and parts[0] == "投":
            result = await self._vote_cast(
                event, parts[1], games.parse_amount(parts[2], 0)
            )
            if result is not None:
                event.stop_event()
                await event.send(result)
                return True

        # 谜语抢答
        result = await self._riddle_guess(event, text)
        if result is not None:
            event.stop_event()
            await event.send(result)
            return True

        # 抢答
        result = await self._rush_check(event, text)
        if result is not None:
            event.stop_event()
            await event.send(result)
            return True

        # 接龙：纯中文词语
        if (
            key in self._chains
            and 2 <= len(text) <= 12
            and all("\u4e00" <= ch <= "\u9fff" for ch in text)
        ):
            result = await self._chain_submit(event, text)
            if result is not None:
                event.stop_event()
                await event.send(result)
                return True

        return False

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
                self._chain_hint_at = {
                    k: v for k, v in self._chain_hint_at.items() if now - v < 60
                }
                self.store.prune_polls(POLL_KEEP, now)
                if len(self._cooldown) > _COOLDOWN_MAX:
                    self._cooldown.clear()
                await self.store.flush_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 后台任务必须吞异常
                logger.error(f"[互动] 后台维护任务异常: {e}")

    # ------------------------------------------------------------------ 签到 / 积分

    @filter.command("签到", alias={"sign", "打卡"})
    async def cmd_sign(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """每日签到领取积分。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("sign_in"):
            return self._deny("签到功能已关闭。")
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
            return event.plain_result(
                f"@{name} {msg}\n当前余额 {user['balance']} {self._unit}，"
                f"已连续签到 {streak} 天。\n{flavor}"
            )
        return event.plain_result(f"@{name} {msg}")

    @filter.command("积分", alias={"余额", "balance", "我的"})
    async def cmd_balance(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查询个人积分、等级、称号与各项统计。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        level, inner, need, total = games.level_of(user)
        progress = games.bar(inner, need)
        title = user.get("title") or "暂无"
        balance_rank = self.store.rank_of(key, uid, "balance")
        rank_text = f"（本群第 {balance_rank} 名）" if balance_rank else "（还没上榜）"
        lucky = games.lucky_number(games.today_str(), uid)
        await self.store.save(key)
        return event.plain_result(
            f"@{name} 你好，\n"
            f"💰 余额：{user['balance']} {self._unit}{rank_text}\n"
            f"🏅 等级：Lv.{level}  {progress} {inner}/{need}（累计 {total} 经验）\n"
            f"🎖 称号：{title}\n"
            f"📅 累计签到 {user['total_sign']} 天（连签 {user['streak']}，最长 {user['best_streak']}）\n"
            f"🎰 抽奖 {user['lottery_count']} 次｜🎯 猜中 {user['guess_win']} 次｜"
            f"🔗 接龙 {user['chain_win']} 次｜🥊 打劫 {user['rob_win']} 胜 {user['rob_lose']} 负\n"
            f"🍀 今日幸运数字：{lucky}"
        )

    @filter.command("转账", alias={"转积分", "pay"})
    async def cmd_transfer(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """把积分转给群里其他成员。

        Args:
            args: ``<目标用户> <数量>``，目标可用 ``@某人`` 或用户 ID。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        parts = self._args(event).split()
        if len(parts) < 2:
            return event.plain_result("用法：转账 @某人 数量，例如：转账 10001 50")
        key = self._session_key(event)
        uid, _ = self._sender(event)
        target = self._resolve_uid(parts[0])
        if not target:
            return event.plain_result("没有识别到收款人，请用 @某人 或直接写用户 ID。")
        amount = games.parse_amount(parts[1], default=-1)
        if amount <= 0:
            return event.plain_result("转账数量必须是不小于 1 的整数。")
        ok, msg = self.store.transfer(key, uid, target, amount)
        if not ok:
            return event.plain_result(msg)
        await self.store.save(key, force=True)
        return event.plain_result(
            f"已转给 {self.store.display_name(key, target)} {amount} {self._unit}。"
        )

    # ------------------------------------------------------------------ 抽奖

    @filter.command("抽奖", alias={"抽卡", "lottery"})
    async def cmd_lottery(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """消耗积分抽奖。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("lottery"):
            return self._deny("抽奖功能已关闭。")
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
            return event.plain_result(f"@{name} {err}")
        return event.plain_result(
            f"@{name} 抽奖结果：{prize}\n"
            f"本次净变动 {delta:+d}，余额 {user['balance']} {self._unit}。"
        )

    # ------------------------------------------------------------------ 猜数字

    @filter.command("猜数字", alias={"猜数", "guess"})
    async def cmd_guess_start(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开始一局猜数字。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("guess_number"):
            return self._deny("猜数字功能已关闭。")
        key = self._session_key(event)
        game = GuessGame.new(
            self._int("guess_number", "min", default=1),
            self._int("guess_number", "max", default=100),
            self._int("guess_number", "max_attempts", default=10),
            rng=self._rng,
        )
        self._guesses[key] = game
        return event.plain_result(
            f"🎯 猜数字开始！范围 {game.low} ~ {game.high}，"
            f"共 {game.max_attempts} 次机会。\n发送「猜 <数字>」来猜，"
            f"猜中得 {self._guess_reward()} {self._unit}。"
        )

    @filter.command("猜", alias={"cai"})
    async def cmd_guess(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """提交猜数字答案。

        Args:
            args: 猜测的数字。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        raw = (args.strip() or self._args(event).strip()).split()
        if not raw or not raw[0].isdigit():
            return event.plain_result("用法：猜 <数字>，例如：猜 50")
        result = await self._guess_once(event, int(raw[0]))
        if result is None:
            return event.plain_result(
                "当前没有进行中的猜数字，发送「猜数字」开始一局吧。"
            )
        return result

    async def _guess_once(
        self, event: AstrMessageEvent, number: int
    ) -> MessageEventResult | None:
        """处理一次猜数字（供指令与自动接管复用）。

        Args:
            event: 消息事件。
            number: 猜测的数字。

        Returns:
            结果；没有进行中的对局时返回 ``None``。
        """
        key = self._session_key(event)
        game = self._guesses.get(key)
        if game is None:
            return None
        if time.time() - game.started_at > GUESS_TTL:
            self._guesses.pop(key, None)
            return event.plain_result("上一局已超时，发送「猜数字」重新开始。")
        uid, name = self._sender(event)
        result, hint = game.guess(number, uid)
        if result in ("win", "lose"):
            self._guesses.pop(key, None)
            if result == "win":
                reward = self._guess_reward()
                user = self.store.get_user(key, uid)
                user["name"] = name
                user["guess_win"] = int(user.get("guess_win", 0) or 0) + 1
                user["balance"] = int(user.get("balance", 0) or 0) + reward
                games.bump_daily(user, "guess")
                await self.store.save(key, force=True)
                hint += f" 奖励 {reward} {self._unit}！"
        return event.plain_result(f"@{name} {hint}")

    # ------------------------------------------------------------------ 词语接龙

    @filter.command("接龙", alias={"词语接龙", "chain"})
    async def cmd_chain_start(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开始一局词语接龙，可带起始词。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("word_chain"):
            return self._deny("接龙功能已关闭。")
        key = self._session_key(event)
        # 必须剥掉指令名后再取起始词，否则 /接龙 互动 会解析成「接龙」
        arg = self._args(event).strip()
        first = arg.split()[0] if arg else ""
        if (
            not first
            or len(first) > 12
            or not all("\u4e00" <= ch <= "\u9fff" for ch in first)
        ):
            first = self._rng.choice(
                ("互动", "科技", "群聊", "开心", "生活", "音乐", "星空")
            )
        game = ChainGame(last_word=first)
        game.used.add(first)
        self._chains[key] = game
        return event.plain_result(
            f"🔗 词语接龙开始！起始词：「{first}」\n"
            f"请发送以「{first[-1]}」开头的两字以上中文词语，直接发词语即可，"
            f"{self._int('word_chain', 'timeout_seconds', default=60)} 秒内接不上就结束。"
        )

    async def _chain_submit(
        self, event: AstrMessageEvent, word: str
    ) -> MessageEventResult | None:
        """处理一次接龙提交（供自动接管复用）。

        只有「轮到别人却接错」的玩家会收到提示，并做节流，避免刷屏。

        Args:
            event: 消息事件。
            word: 提交的词语。

        Returns:
            结果；无进行中的对局或提交流程无需回应时返回 ``None``。
        """
        if not self._feature_on("word_chain"):
            return None
        key = self._session_key(event)
        game = self._chains.get(key)
        if game is None:
            return None
        timeout = self._int("word_chain", "timeout_seconds", default=60)
        if time.time() - game.started_at > timeout:
            self._chains.pop(key, None)
            return None

        uid, name = self._sender(event)
        ok, hint = game.submit(word, uid)
        if not ok:
            if game.last_user and uid != game.last_user:
                now = time.time()
                last = self._chain_hint_at.get(key, 0.0)
                if now - last >= _CHAIN_HINT_COOLDOWN:
                    self._chain_hint_at[key] = now
                    return event.plain_result(hint)
            return None

        game.started_at = time.time()
        reward = self._int("word_chain", "reward", default=5)
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["chain_win"] = int(user.get("chain_win", 0) or 0) + 1
        user["balance"] = int(user.get("balance", 0) or 0) + reward
        games.bump_daily(user, "chain")
        await self.store.save(key)
        flavor = self._rng.choice(games.CHAIN_FLAVORS)
        return event.plain_result(f"@{name} {hint}（+{reward} {self._unit}）{flavor}")

    # ------------------------------------------------------------------ 投票

    @filter.command("投票")
    async def cmd_vote_create(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """发起投票，用法：/投票 问题 | 选项1 | 选项2 ..."""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("vote"):
            return self._deny("投票功能已关闭。")
        # 无参数时旧代码会 IndexError，这里先做长度校验
        raw = self._args(event).strip()
        parts = [p.strip() for p in raw.replace("｜", "|").split("|") if p.strip()]
        if len(parts) < 3:
            return event.plain_result(
                "用法：/投票 问题 | 选项1 | 选项2\n例如：/投票 晚饭吃啥 | 火锅 | 烧烤"
            )
        question, options = parts[0], parts[1:]
        max_options = max(2, self._int("vote", "max_options", default=10))
        if len(options) > max_options:
            return event.plain_result(
                f"选项最多 {max_options} 个，当前 {len(options)} 个。"
            )
        key = self._session_key(event)
        uid, name = self._sender(event)
        duration = max(30, self._int("vote", "duration_seconds", default=300))
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

        lines = [
            f"📊 投票：{question}",
            f"发起人 @{name}｜编号 {poll_id}｜时长 {games.format_duration(duration)}",
        ]
        lines += [f"{i}. {opt}" for i, opt in enumerate(poll["options"], 1)]
        lines.append(f"参与方式：/投 {poll_id} <选项序号>")
        return event.plain_result("\n".join(lines))

    @filter.command("投")
    async def cmd_vote_cast(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """为指定投票选项投票。

        Args:
            args: ``<投票编号> <选项序号>``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        parts = self._args(event).split()
        if len(parts) < 2:
            return event.plain_result("用法：投 <编号> <选项序号>，例如：投 a1b2 1")
        return await self._vote_cast(event, parts[0], games.parse_amount(parts[1], 0))

    async def _vote_cast(
        self, event: AstrMessageEvent, poll_id: str, option: int
    ) -> MessageEventResult | None:
        """为指定投票选项投票（供指令与自动接管复用）。

        Args:
            event: 消息事件。
            poll_id: 投票编号。
            option: 选项序号，从 1 开始。

        Returns:
            结果；编号不存在时返回 ``None``（避免无关数字误触发回复）。
        """
        key = self._session_key(event)
        poll = self.store.polls(key).get(str(poll_id).lstrip("#").strip())
        if not poll:
            return None
        options = poll.get("options") or []
        if not 1 <= option <= len(options):
            return event.plain_result(f"选项序号应为 1 ~ {len(options)}。")
        uid, _ = self._sender(event)
        votes = poll.setdefault("votes", {})
        if uid in votes:
            return event.plain_result("你已经投过票啦，一人一票哦。")
        votes[uid] = option - 1
        await self.store.save(key, force=True)
        return event.plain_result(
            f"✅ 已投票：{options[option - 1]}（当前共 {len(votes)} 票）"
        )

    @filter.command("投票结果", alias={"查看投票"})
    async def cmd_vote_result(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看投票结果；不带编号时列出本会话全部投票。

        Args:
            args: 可选投票编号。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        polls = self.store.polls(key)
        poll_id = self._args(event).split()[0] if self._args(event).split() else ""
        if not poll_id:
            if not polls:
                return event.plain_result(
                    "本群还没有投票，发送「投票 问题 | 选项 | 选项」发起一个。"
                )
            lines = ["📋 本群投票列表"]
            for pid, poll in list(polls.items())[-10:]:
                lines.append(
                    f"· {pid}｜{poll.get('question', '')}｜{len(poll.get('votes', {}))} 票"
                )
            lines.append("查看详情：/投票结果 <编号>")
            return event.plain_result("\n".join(lines))
        poll = polls.get(str(poll_id).lstrip("#").strip())
        if not poll:
            return event.plain_result(f"没有找到编号为 {poll_id} 的投票。")
        options = poll.get("options") or []
        counts = [0] * len(options)
        for idx in (poll.get("votes") or {}).values():
            if isinstance(idx, int) and 0 <= idx < len(counts):
                counts[idx] += 1
        total = sum(counts)
        lines = [f"📊 {poll.get('question', '')}（共 {total} 票）"]
        width = 12
        for i, (opt, cnt) in enumerate(zip(options, counts, strict=False), 1):
            pct = (cnt / total * 100) if total else 0
            lines.append(
                f"{i}. {opt} — {cnt} 票 {pct:.1f}% {games.bar(cnt, max(1, total), width)}"
            )
        if total:
            winner = options[counts.index(max(counts))]
            lines.append(f"🏆 当前领先：{winner}")
        return event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 掷骰 / 打劫

    @filter.command("掷骰", alias={"骰子", "dice", "roll"})
    async def cmd_dice(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """掷骰子。

        支持 ``/掷骰 3 20`` 与 ``NdM`` 两种写法；参数非数字或缺失时
        回退到 ``1d6``，并会告诉用户实际用了什么。

        Args:
            args: ``<数量> <面数>`` 或 ``NdM``，可省略。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("dice", default=False):
            return self._deny("掷骰子功能已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        count, faces, note = self._dice_params(args.strip() or self._args(event))
        rolls, total = games.roll_dice(count, faces, rng=self._rng)
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["dice_count"] = int(user.get("dice_count", 0) or 0) + 1
        games.bump_daily(user, "dice")
        await self.store.save(key)
        detail = games.dice_faces_text(rolls, faces)
        flavor = self._rng.choice(games.DICE_FLAVORS)
        tail = f"\n{note}" if note else ""
        return event.plain_result(
            f"{flavor} @{name} {count}d{faces}：{detail} = {total}{tail}"
        )

    @filter.command("打劫", alias={"抢劫", "rob"})
    async def cmd_rob(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """打劫群友的积分。

        Args:
            args: ``<目标用户> <数量>``（数量会被自动收敛到安全上限）。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("rob", default=False):
            return self._deny("打劫功能已关闭。")
        parts = self._args(event).split()
        if len(parts) < 2:
            return event.plain_result("用法：打劫 @某人 数量，例如：打劫 10001 50")
        key = self._session_key(event)
        uid, name = self._sender(event)
        target = self._resolve_uid(parts[0])
        if not target:
            return event.plain_result("没有识别到目标，请用 @某人 或直接写用户 ID。")
        if target == uid:
            return event.plain_result("不能打劫自己哦。")
        amount = games.parse_amount(parts[1], default=0)
        if amount <= 0:
            return event.plain_result("打劫数量必须是不小于 1 的整数。")
        victim = self.store.peek_user(key, target)
        if victim is None:
            return event.plain_result("对方在本群还没有档案，无法打劫。")
        attacker = self.store.get_user(key, uid)
        attacker["name"] = name
        attacker_balance = int(attacker.get("balance", 0) or 0)
        victim_balance = int(victim.get("balance", 0) or 0)
        cap = games.rob_max_amount(attacker_balance, victim_balance)
        ok, delta, msg = games.rob_check(attacker, victim, amount, rng=self._rng)
        if ok:
            games.bump_daily(attacker, "rob")
        await self.store.save(key, force=True)
        victim_name = self.store.display_name(key, target)
        tip = ""
        if cap and amount > cap:
            tip = f"\n（按安全上限收敛为 {cap}，打劫金额最多是你的余额的 1/6）"
        return event.plain_result(
            f"@{name} {msg}\n目标：{victim_name}（变动 {delta:+d}）{tip}"
        )

    # ------------------------------------------------------------------ 排行榜

    _RANK_ALIASES: dict[str, str] = {
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
        "掷骰": "dice_count",
        "dice": "dice_count",
        "幸运": "lucky_hit",
        "lucky": "lucky_hit",
    }
    _RANK_TITLES: dict[str, str] = {
        "balance": "积分",
        "total_sign": "签到",
        "lottery_count": "抽奖",
        "guess_win": "猜中",
        "chain_win": "接龙",
        "rob_win": "打劫",
        "dice_count": "掷骰",
        "lucky_hit": "幸运数字命中",
    }

    @filter.command("排行榜", alias={"排行", "rank", "榜单"})
    async def cmd_rank(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看本群排行榜。

        参数默认值是空字符串而不是「积分」——否则用户输入一个非法维度时，
        框架会把参数解析失败的值丢弃、用默认值兜底，指令就会「看起来没报错
        但也没按用户说的做」。这里对「没填」和「填错」分别处理。

        Args:
            args: 排行维度，见 ``_RANK_ALIASES``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        raw = args.strip() or self._args(event).strip()
        field = self._RANK_ALIASES.get(raw) if raw else "balance"
        if field is None:
            dims = " / ".join(dict.fromkeys(self._RANK_TITLES.values()))
            return event.plain_result(f"可排行维度：{dims}。例如：/排行榜 签到")
        key = self._session_key(event)
        size = max(3, min(50, self._int("rank", "size", default=10)))
        rows = self.store.top_users(key, by=field, limit=size)
        if not rows:
            return event.plain_result(
                f"本群还没有{self._RANK_TITLES[field]}数据，快去玩一局吧！"
            )

        medals = ("🥇", "🥈", "🥉")
        lines = [f"🏆 本群{self._RANK_TITLES[field]}排行榜（Top {len(rows)}）"]
        for i, (uid, value) in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i + 1:>2}."
            unit = f" {self._unit}" if field == "balance" else ""
            lines.append(
                f"{prefix} {self.store.display_name(key, uid)} — {value}{unit}"
            )
        return event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 每日任务

    @filter.command("每日任务", alias={"任务", "quest"})
    async def cmd_quest(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看每日任务进度。"""
        if reason := self._guard(event):
            return self._deny(reason)
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
                f"{mark} {quest['desc']}（{done_count}/{target}）"
                f"→ {quest['reward']} {self._unit}"
            )
        lines.append("完成任意任务后发送「/领取 <任务名>」领取奖励。")
        lines.append("任务名：" + " / ".join(q[0]["code"] for q in progress))
        await self.store.save(key)
        return event.plain_result("\n".join(lines))

    @filter.command("领取", alias={"领奖", "claim"})
    async def cmd_claim(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """领取每日任务奖励；不带参数时一键领取所有可领任务。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        code = self._args(event).strip().split()[0] if self._args(event).strip() else ""

        if not code:
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
                return event.plain_result(
                    "暂时没有可领取的任务奖励，发送「每日任务」查看进度。"
                )
            await self.store.save(key, force=True)
            return event.plain_result(
                f"@{name} 一键领取 {claimed} 个任务，共获得 {total_reward} {self._unit}！\n"
                + "\n".join(notes)
            )

        ok, reward, msg = games.claim_quest(user, code)
        if ok:
            await self.store.save(key, force=True)
        return event.plain_result(f"@{name} {msg}")

    # ------------------------------------------------------------------ 幸运数字 / 八球 / 扎心

    @filter.command("幸运数字", alias={"幸运", "lucky"})
    async def cmd_lucky(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看今日幸运数字，或检验自己的数字是否命中。

        Args:
            args: 可选，要检验的数字。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("lucky"):
            return self._deny("幸运数字已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        today = games.today_str()
        lucky = games.lucky_number(today, uid)
        arg = self._args(event).strip()
        user = self.store.get_user(key, uid)
        user["name"] = name

        if not arg:
            await self.store.save(key)
            return event.plain_result(
                f"🍀 @{name} 今天的幸运数字是 {lucky}。\n"
                f"发送「幸运数字 <你猜的数字>」看看是否命中，"
                f"命中可获得 {self._int('lucky', 'reward', default=15)} {self._unit}。"
            )

        try:
            value = int(arg)
        except (TypeError, ValueError):
            return event.plain_result("请发送一个 1~100 的整数。")
        reward = max(1, self._int("lucky", "reward", default=15))
        if not games.lucky_hit(today, uid, value):
            await self.store.save(key)
            return event.plain_result(
                f"@{name} {value} 不是今天的幸运数字，再想想～（每天都可以重新猜一次）"
            )
        if user.get("lucky_last") == today:
            await self.store.save(key)
            return event.plain_result(
                f"@{name} 你今天已经领过幸运数字奖励啦，明天再来。"
            )
        user["lucky_last"] = today
        user["lucky_hit"] = int(user.get("lucky_hit", 0) or 0) + 1
        user["balance"] = int(user.get("balance", 0) or 0) + reward
        await self.store.save(key, force=True)
        return event.plain_result(
            f"🎉 @{name} 猜对了！今天的幸运数字就是 {lucky}，"
            f"获得 {reward} {self._unit}。"
        )

    @filter.command("八球", alias={"魔法八球", "8ball"})
    async def cmd_eight_ball(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """魔法八球：给一个是/否问题一个答案。

        Args:
            args: 你的问题。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("eight_ball"):
            return self._deny("魔法八球已关闭。")
        q = args.strip() or self._args(event).strip()
        _, answer = games.eight_ball(q, rng=self._rng)
        return event.plain_result(answer)

    @filter.command("扎心", alias={"扎心话", "roast"})
    async def cmd_roast(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """随机来一句扎心文案。

        Args:
            args: 可选，被扎心的对象（@某人 或 ID）。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("roast"):
            return self._deny("扎心文案已关闭。")
        arg = (args.strip() or self._args(event).strip()).lstrip("@").strip()
        key = self._session_key(event)
        _, name = self._sender(event)
        shown = self.store.display_name(key, arg) if arg else name
        return event.plain_result(games.roast(shown, rng=self._rng))

    # ------------------------------------------------------------------ 帮助 / 状态

    @filter.command("互动", alias={"互动帮助", "hd", "help"})
    async def cmd_help(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看互动插件帮助。"""
        dims = " / ".join(dict.fromkeys(self._RANK_TITLES.values()))
        return event.plain_result(
            "🎮 互动插件 · 玩法总览\n"
            "群里直接发指令即可，无需 @我（/指令、!指令 同样有效）\n"
            "\n"
            "【日常】\n"
            "签到 ｜ 积分 ｜ 每日任务 ｜ 领取 [任务名]\n"
            "\n"
            "【娱乐】\n"
            "抽奖 ｜ 猜数字 → 直接发数字 ｜ 接龙 [起始词] → 直接发词语\n"
            "掷骰 [数量] [面数] ｜ 幸运数字 [数字] ｜ 八球 <问题>\n"
            "占卜 ｜ 笑话 ｜ 随机 选项1|选项2\n"
            "\n"
            "【小游戏】\n"
            "21点 [下注] → 要牌 / 停牌 ｜ 数字炸弹 → 直接发数字\n"
            "猜谜 → 直接发答案 ｜ 海龟汤 → 海龟汤 <问题> / 汤底\n"
            "谁最先 [目标词] → 抢答\n"
            "\n"
            "【社交】\n"
            "转账 <用户> <数量> ｜ 打劫 <用户> <数量> ｜ 扎心 [@某人]\n"
            "赠送 @某人 奶茶 ｜ 亲密度 [@某人] ｜ 表白 @某人 ｜ pk @某人 [赌注]\n"
            "\n"
            "【商店与排行】\n"
            "商店 → 购买 <道具名> ｜ 背包 ｜ 佩戴 <称号名>\n"
            f"排行榜 <维度>（{dims}）\n"
            "\n"
            "【工具】\n"
            "投票 问题 | 选项1 | 选项2 → 投 <编号> <序号> → 投票结果 [编号]\n"
            "互动状态 ｜ 互动统计\n"
            "\n"
            f"当前积分单位：{self._unit}｜作者：科技酱"
        )

    @filter.command("互动状态", alias={"hd状态"})
    async def cmd_status(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看插件运行状态（管理员）。"""
        if not event.is_admin():
            return event.plain_result("只有管理员可以查看运行状态。")
        stats = self.store.stats()
        return event.plain_result(
            "⚙️ 互动插件运行状态\n"
            f"版本 {self._version()}｜作者 科技酱\n"
            f"缓存会话 {stats['cached_sessions']}｜缓存用户 {stats['cached_users']}\n"
            f"缓存投票 {stats['cached_polls']}｜待落盘 {stats['dirty_sessions']}\n"
            f"磁盘文件 {stats['disk_files']}｜猜数字局 {len(self._guesses)}"
            f"｜接龙局 {len(self._chains)}\n"
            f"数据目录：{self.store.data_dir}"
        )

    # ------------------------------------------------------------------ 扩展玩法：21 点

    @filter.command("21点", alias={"黑杰克", "blackjack", "bj"})
    async def cmd_blackjack(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开一局 21 点，可选下注额。

        Args:
            args: 可选下注积分。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("blackjack"):
            return self._deny("21 点已关闭。")
        key = self._session_key(event)
        ongoing = self._blackjack.get(key)
        if ongoing is not None and not ongoing.finished:
            return event.plain_result(
                "你还有一局 21 点没结束，先「要牌」或「停牌」吧。"
            )

        lo = max(1, self._int("blackjack", "min_bet", default=10))
        hi = max(lo, self._int("blackjack", "max_bet", default=500))
        bet = (
            games.parse_amount(self._args(event).split()[0], lo)
            if self._args(event).split()
            else lo
        )
        bet = max(lo, min(hi, bet))
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        if int(user["balance"]) < bet:
            return event.plain_result(
                f"余额不足，最低下注 {lo} {self._unit}，你只有 {user['balance']}。"
            )
        user["balance"] = int(user["balance"]) - bet
        game = games_extra.BlackjackGame.new(bet, rng=self._rng)
        if game.finished:  # 起手黑杰克，直接结算
            return await self._settle_blackjack(event, key, game, "win")
        self._blackjack[key] = game
        await self.store.save(key)
        return event.plain_result(
            f"🃏 21 点开始！下注 {bet} {self._unit}\n"
            f"你的手牌：{self._cards(game.player)}（{game.player_value()} 点）\n"
            f"庄家明牌：{self._cards(game.dealer[:1])}\n"
            f"发送「要牌」继续，或「停牌」结算。"
        )

    @staticmethod
    def _cards(cards: list[int]) -> str:
        """把牌面渲染成 ``A♠ 10♥`` 形式。

        Args:
            cards: 牌面点数列表。

        Returns:
            可读文本。
        """
        names = {1: "A", 11: "J", 12: "Q", 13: "K"}
        suits = ("♠", "♥", "♣", "♦")
        return " ".join(
            f"{names.get(c, str(c))}{suits[i % 4]}" for i, c in enumerate(cards)
        )

    @filter.command("要牌", alias={"hit", "再来一张"})
    async def cmd_bj_hit(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """21 点要牌。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        game = self._blackjack.get(key)
        if game is None or game.finished:
            return event.plain_result("当前没有进行中的 21 点，发送「21点」开一局吧。")
        outcome = game.hit(rng=self._rng)
        if outcome == "continue":
            return event.plain_result(
                f"🃏 抽到新牌，你的手牌：{self._cards(game.player)}"
                f"（{game.player_value()} 点）\n继续「要牌」或「停牌」。"
            )
        self._blackjack.pop(key, None)
        return await self._settle_blackjack(event, key, game, outcome)

    @filter.command("停牌", alias={"stand", "不玩了"})
    async def cmd_bj_stand(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """21 点停牌结算。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        game = self._blackjack.get(key)
        if game is None or game.finished:
            return event.plain_result("当前没有进行中的 21 点，发送「21点」开一局吧。")
        self._blackjack.pop(key, None)
        return await self._settle_blackjack(event, key, game, game.stand(rng=self._rng))

    async def _settle_blackjack(
        self,
        event: AstrMessageEvent,
        key: str,
        game: games_extra.BlackjackGame,
        result: str,
    ) -> MessageEventResult:
        """结算 21 点并返还积分。

        Args:
            event: 消息事件。
            key: 会话标识。
            game: 对局。
            result: 结算结果。

        Returns:
            结算文案。
        """
        payout = game.payout(result)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        if result == "win":
            user["bj_win"] = int(user.get("bj_win", 0) or 0) + 1
        if payout:
            user["balance"] = int(user.get("balance", 0) or 0) + payout
        games.bump_daily(user, "blackjack")
        await self.store.save(key, force=True)
        text = {
            "win": "🎉 你赢了！",
            "lose": "😢 庄家赢了，下次再来。",
            "push": "🤝 平局，下注已退还。",
            "bust": "💥 爆牌了！",
        }.get(result, "对局结束。")
        return event.plain_result(
            f"{text} @{name}\n"
            f"你的手牌：{self._cards(game.player)}（{game.player_value()} 点）\n"
            f"庄家手牌：{self._cards(game.dealer)}（{game.dealer_value()} 点）\n"
            f"返还 {payout} {self._unit}，余额 {user['balance']}。"
        )

    # ------------------------------------------------------------------ 扩展玩法：数字炸弹

    @filter.command("数字炸弹", alias={"炸弹", "bomb"})
    async def cmd_bomb_start(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """埋一颗数字炸弹。

        Args:
            args: 可选自定义区间 ``最小 最大``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("bomb"):
            return self._deny("数字炸弹已关闭。")
        lo = self._int("bomb", "min", default=1)
        hi = self._int("bomb", "max", default=100)
        nums = [int(x) for x in self._args(event).split() if x.isdigit()]
        if len(nums) >= 2:
            lo, hi = nums[0], nums[1]
        lo, hi = games.normalize_range(lo, hi, min_span=10)
        key = self._session_key(event)
        game = games_extra.BombGame.new(lo, hi, rng=self._rng)
        self._bombs[key] = game
        return event.plain_result(
            f"💣 数字炸弹已埋好！范围 {game.low} ~ {game.high - 1}。\n"
            f"大家轮流直接发数字，踩中炸弹的人会被扣分哦。"
        )

    async def _bomb_report(
        self, event: AstrMessageEvent, value: int
    ) -> MessageEventResult | None:
        """处理一次报数（供自动接管复用）。

        Args:
            event: 消息事件。
            value: 报出的数字。

        Returns:
            结果；无进行中的对局时返回 ``None``。
        """
        if not self._feature_on("bomb"):
            return None
        key = self._session_key(event)
        game = self._bombs.get(key)
        if game is None:
            return None
        timeout = self._int("bomb", "timeout_seconds", default=180)
        if time.time() - game.started_at > timeout:
            self._bombs.pop(key, None)
            return None
        uid, name = self._sender(event)
        boom, msg = game.report(value, uid)
        user = self.store.get_user(key, uid)
        user["name"] = name
        if boom:
            self._bombs.pop(key, None)
            punish = max(0, self._int("bomb", "punish", default=10))
            user["balance"] = max(0, int(user.get("balance", 0) or 0) - punish)
            await self.store.save(key, force=True)
            return event.plain_result(
                f"{msg}\n@{name} 被炸飞，扣 {punish} {self._unit}（余额 {user['balance']}）。"
            )
        reward = max(0, self._int("bomb", "reward", default=1))
        user["bomb_win"] = int(user.get("bomb_win", 0) or 0) + 1
        if reward:
            user["balance"] = int(user.get("balance", 0) or 0) + reward
        games.bump_daily(user, "bomb")
        await self.store.save(key)
        return event.plain_result(f"@{name} {msg}（+{reward} {self._unit}）")

    # ------------------------------------------------------------------ 扩展玩法：猜谜 / 海龟汤 / 抢答

    @filter.command("猜谜", alias={"谜语", "出题", "riddle"})
    async def cmd_riddle(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """出一道谜语让大家抢答。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("riddle"):
            return self._deny("猜谜已关闭。")
        key = self._session_key(event)
        question, answer, hint = games_extra.random_riddle(self._rng)
        timeout = max(10, self._int("riddle", "timeout_seconds", default=60))
        self._riddles[key] = {
            "question": question,
            "answer": answer,
            "hint": hint,
            "started_at": time.time(),
        }
        return event.plain_result(
            f"🧩 谜语：{question}\n（{timeout} 秒内直接发答案，第一位答对者获奖）"
        )

    @filter.command("谜底", alias={"答案", "公布答案"})
    async def cmd_riddle_answer(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """公布当前谜语的谜底。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        riddle = self._riddles.pop(key, None)
        if not riddle:
            return event.plain_result("当前没有进行中的谜语，发送「猜谜」出一道吧。")
        return event.plain_result(
            f"💡 谜底是：{riddle['answer']}\n（提示：{riddle['hint']}）"
        )

    async def _riddle_guess(
        self, event: AstrMessageEvent, text: str
    ) -> MessageEventResult | None:
        """处理谜语抢答（供自动接管复用）。

        Args:
            event: 消息事件。
            text: 玩家发送的文本。

        Returns:
            结果；未命中时返回 ``None``。
        """
        if not self._feature_on("riddle"):
            return None
        key = self._session_key(event)
        riddle = self._riddles.get(key)
        if not riddle:
            return None
        timeout = max(10, self._int("riddle", "timeout_seconds", default=60))
        if time.time() - float(riddle["started_at"]) > timeout * 3:
            self._riddles.pop(key, None)
            return None
        if self._normalize(text) != self._normalize(str(riddle["answer"])):
            return None
        self._riddles.pop(key, None)
        uid, name = self._sender(event)
        reward = max(0, self._int("riddle", "reward", default=20))
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["balance"] = int(user.get("balance", 0) or 0) + reward
        user["riddle_win"] = int(user.get("riddle_win", 0) or 0) + 1
        games.bump_daily(user, "riddle")
        await self.store.save(key, force=True)
        return event.plain_result(
            f"🎉 恭喜 @{name} 答对了！谜底就是「{riddle['answer']}」，+{reward} {self._unit}。"
        )

    @filter.command("海龟汤", alias={"turtle", "汤"})
    async def cmd_soup(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开始一局海龟汤，或对当前汤面提问。

        Args:
            args: 可选提问内容。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("turtle_soup"):
            return self._deny("海龟汤已关闭。")
        key = self._session_key(event)
        ask = self._args(event).strip()
        if ask:
            soup = self._soups.get(key)
            if not soup:
                return event.plain_result(
                    "当前没有进行中的海龟汤，发送「海龟汤」开一局吧。"
                )
            return event.plain_result(
                f"🍲 收到提问：「{ask}」\n"
                f"（纯文本模式下无法自动判定，想揭晓答案可发送「汤底」）"
            )
        title, face, answer = games_extra.random_soup(self._rng)
        self._soups[key] = {
            "title": title,
            "face": face,
            "answer": answer,
            "started_at": time.time(),
        }
        return event.plain_result(
            f"🍲【{title}】\n{face}\n\n"
            f"发送「海龟汤 你的问题」提问，或发送「汤底」直接看答案。"
        )

    @filter.command("汤底", alias={"soupanswer"})
    async def cmd_soup_answer(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """公布当前海龟汤的汤底。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        soup = self._soups.pop(key, None)
        if not soup:
            return event.plain_result(
                "当前没有进行中的海龟汤，发送「海龟汤」开一局吧。"
            )
        return event.plain_result(f"🍲 汤底：{soup['answer']}")

    @filter.command("谁最先", alias={"抢答", "rush", "手速"})
    async def cmd_rush(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开始抢答：谁最先发出指定内容。

        Args:
            args: 可选目标词。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("rush"):
            return self._deny("抢答已关闭。")
        key = self._session_key(event)
        keyword = self._args(event).strip() or "我最快"
        timeout = max(5, self._int("rush", "timeout_seconds", default=30))
        reward = max(0, self._int("rush", "reward", default=10))
        self._rushes[key] = games_extra.RushGame(keyword=keyword, reward=reward)
        return event.plain_result(
            f"⚡ 抢答开始！最先发出「{keyword}」的人获胜（{timeout} 秒内）。直接发就行！"
        )

    async def _rush_check(
        self, event: AstrMessageEvent, text: str
    ) -> MessageEventResult | None:
        """检查抢答结果（供自动接管复用）。

        Args:
            event: 消息事件。
            text: 玩家发送的文本。

        Returns:
            结果；未命中时返回 ``None``。
        """
        if not self._feature_on("rush"):
            return None
        key = self._session_key(event)
        game = self._rushes.get(key)
        if game is None or game.winner:
            return None
        timeout = max(5, self._int("rush", "timeout_seconds", default=30))
        if game.expired(timeout):
            self._rushes.pop(key, None)
            return None
        uid, name = self._sender(event)
        ok, msg = game.check(text, uid, timeout)
        if not ok:
            return None
        self._rushes.pop(key, None)
        reward = game.reward
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["balance"] = int(user.get("balance", 0) or 0) + reward
        games.bump_daily(user, "rush")
        await self.store.save(key, force=True)
        return event.plain_result(
            f"{msg}\n@{name} 余额 {user['balance']} {self._unit}。"
        )

    # ------------------------------------------------------------------ 扩展玩法：运势 / 商店 / 社交

    @filter.command("每日运势", alias={"运势", "占卜", "fortune", "算命"})
    async def cmd_fortune(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看今日运势（同一天结果稳定）。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("fortune"):
            return self._deny("运势功能已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        cost = max(0, self._int("fortune", "cost", default=0))
        user = self.store.get_user(key, uid)
        user["name"] = name
        if cost:
            if int(user["balance"]) < cost:
                return event.plain_result(f"占卜需要 {cost} {self._unit}，余额不足。")
            user["balance"] = int(user["balance"]) - cost
        result = games_extra.daily_fortune(f"{key}:{uid}")
        games.bump_daily(user, "fortune")
        await self.store.save(key, force=True)
        tail = f"\n（本次消耗 {cost} {self._unit}）" if cost else ""
        return event.plain_result(
            f"🔮 @{name} {result['date']} 运势 · 生肖{games_extra.zodiac_of()}\n"
            f"整体：{result['level']}　{result['star']}\n"
            f"幸运值：{result['luck']}/99\n"
            f"关键词：{'、'.join(result['aspects'])}\n"
            f"今日忠告：{result['tip']}{tail}"
        )

    @filter.command("商店", alias={"称号", "shop", "道具店"})
    async def cmd_shop(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看称号 / 头像框 / 消耗品商店。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("shop"):
            return self._deny("商店已关闭。")
        lines = [f"🛒 互动商店（货币：{self._unit}）"]
        for kind, header in (
            ("title", "【称号】"),
            ("frame", "【头像框】"),
            ("consumable", "【消耗品】"),
        ):
            lines.append(header)
            for _item_id, (
                name,
                price,
                item_kind,
                desc,
            ) in games_extra.SHOP_ITEMS.items():
                if item_kind == kind:
                    lines.append(f"· {name} —— {price} {self._unit}（{desc}）")
        lines.append("发送「购买 <道具名>」即可购买，例如：购买 称号·大佬")
        return event.plain_result("\n".join(lines))

    @filter.command("购买", alias={"buy", "兑换"})
    async def cmd_buy(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """购买商店道具。

        Args:
            args: 道具名称或 ID。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("shop"):
            return self._deny("商店已关闭。")
        if not self._bool("shop", "allow_buy", default=True):
            return self._deny("当前不允许购买道具。")
        query = self._args(event).strip()
        if not query:
            return event.plain_result("用法：购买 <道具名>，可先发「商店」查看列表。")
        found = games_extra.resolve_shop_item(query)
        if not found:
            return event.plain_result(
                f"没有找到道具「{query}」，可发「商店」查看列表。"
            )
        item_id, name, price, kind, _desc = found
        key = self._session_key(event)
        uid, uname = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = uname
        if int(user["balance"]) < price:
            return event.plain_result(
                f"余额不足，{name} 需要 {price} {self._unit}，你只有 {user['balance']}。"
            )
        user["balance"] = int(user["balance"]) - price
        note = ""
        if kind == "title":
            title_name = name.split("·", 1)[-1]
            if title_name in (user.get("titles") or []):
                user["balance"] = int(user["balance"]) + price
                return event.plain_result(
                    f"你已经拥有称号「{title_name}」，已退还 {price} {self._unit}。"
                )
            user.setdefault("titles", []).append(title_name)
            user["title"] = title_name
            note = f"\n已解锁并佩戴称号「{title_name}」。"
        elif kind == "frame":
            frame_name = name.split("·", 1)[-1]
            if frame_name in (user.get("frames") or []):
                user["balance"] = int(user["balance"]) + price
                return event.plain_result(
                    f"你已经拥有头像框「{frame_name}」，已退还 {price} {self._unit}。"
                )
            user.setdefault("frames", []).append(frame_name)
            note = f"\n已解锁头像框「{frame_name}」。"
        else:
            bag = user.setdefault("bag", {})
            bag[item_id] = int(bag.get(item_id, 0) or 0) + 1
            note = "\n已放入背包。"
        await self.store.save(key, force=True)
        return event.plain_result(
            f"✅ 购买成功：{name}，花费 {price} {self._unit}，余额 {user['balance']}。{note}"
        )

    @filter.command("背包", alias={"道具", "bag", "inventory"})
    async def cmd_bag(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看背包与已解锁称号。

        Args:
            args: 可选 @某人。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        target = self._resolve_uid(self._args(event)) or str(event.get_sender_id())
        user = self.store.get_user(key, target)
        name = self.store.display_name(key, target)
        bag = user.get("bag") or {}
        lines = [f"🎒 {name} 的背包"]
        if bag:
            lines += [
                f"· {games_extra.item_display_name(iid)} ×{cnt}"
                for iid, cnt in bag.items()
            ]
        else:
            lines.append("（空空如也）")
        titles = user.get("titles") or []
        lines.append(f"\n🏷 已解锁称号：{'、'.join(titles) if titles else '暂无'}")
        lines.append(f"当前佩戴：{user.get('title') or '无'}")
        frames = user.get("frames") or []
        lines.append(f"🖼 头像框：{'、'.join(frames) if frames else '暂无'}")
        return event.plain_result("\n".join(lines))

    @filter.command("佩戴", alias={"wear", "装备"})
    async def cmd_wear(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """佩戴或卸下称号。

        Args:
            args: 称号名，留空表示卸下。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        title = self._args(event).strip()
        if not title:
            user["title"] = ""
            await self.store.save(key, force=True)
            return event.plain_result("已卸下称号。")
        if title not in (user.get("titles") or []):
            return event.plain_result(
                f"你还没有称号「{title}」，可用「商店」购买或抽奖获得。"
            )
        user["title"] = title
        await self.store.save(key, force=True)
        return event.plain_result(f"已佩戴称号「{title}」。")

    @filter.command("赠送", alias={"送礼", "gift"})
    async def cmd_gift(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """赠送礼物给群友，提升亲密度。

        Args:
            args: ``<@某人> <礼物名> [数量]``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("social"):
            return self._deny("社交玩法已关闭。")
        parts = self._args(event).split()
        if len(parts) < 2:
            return event.plain_result(
                "用法：赠送 @某人 礼物，可送：" + "、".join(games_extra.GIFT_ITEMS)
            )
        key = self._session_key(event)
        uid, name = self._sender(event)
        target = self._resolve_uid(parts[0])
        if not target or target == uid:
            return event.plain_result("请 @ 一位群友（不能送给自己）。")
        gift = parts[1]
        if gift not in games_extra.GIFT_ITEMS:
            return event.plain_result(
                "没有这种礼物哦，可送：" + "、".join(games_extra.GIFT_ITEMS)
            )
        count = max(
            1, min(99, games.parse_amount(parts[2], 1) if len(parts) > 2 else 1)
        )
        emoji, price, base = games_extra.GIFT_ITEMS[gift]
        total = price * count
        user = self.store.get_user(key, uid)
        user["name"] = name
        if int(user["balance"]) < total:
            return event.plain_result(
                f"余额不足，{count} 份{gift}需要 {total} {self._unit}。"
            )
        per = max(0, self._int("social", "gift_intimacy", default=5))
        gained = per * count + base * count
        user["balance"] = int(user["balance"]) - total
        user["gift_sent"] = int(user.get("gift_sent", 0) or 0) + count
        receiver = self.store.get_user(key, target)
        receiver["gift_received"] = int(receiver.get("gift_received", 0) or 0) + count
        bucket = user.setdefault("intimacy", {})
        points, stage = games_extra.add_intimacy(bucket, uid, target, gained)
        games.bump_daily(user, "gift")
        await self.store.save(key, force=True)
        return event.plain_result(
            f"{emoji} @{name} 送出了 {count} 份{gift}给 "
            f"{self.store.display_name(key, target)}！\n"
            f"花费 {total} {self._unit}，亲密度 +{gained}，当前 {points}（{stage}）。"
        )

    @filter.command("亲密度", alias={"好感度", "intimacy", "cp"})
    async def cmd_intimacy(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看亲密度与关系；不带参数时列出亲密度排行。

        Args:
            args: 可选 @某人。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("social"):
            return self._deny("社交玩法已关闭。")
        key = self._session_key(event)
        uid, _name = self._sender(event)
        user = self.store.get_user(key, uid)
        bucket = user.get("intimacy") or {}
        target = self._resolve_uid(self._args(event))
        if target:
            points = games_extra.get_intimacy(bucket, uid, target)
            return event.plain_result(
                f"💞 你与 {self.store.display_name(key, target)} 的亲密度："
                f"{points}（{games_extra.relation_stage(points)}）"
            )
        if not bucket:
            return event.plain_result("你还没有和群友互动过，试试「赠送 @某人 奶茶」。")
        rows = []
        for pair, points in bucket.items():
            a, _, b = str(pair).partition("|")
            other = b if str(a) == uid else (a if str(b) == uid else "")
            if other:
                rows.append((other, int(points)))
        if not rows:
            return event.plain_result("你还没有和群友互动过，试试「赠送 @某人 奶茶」。")
        rows.sort(key=lambda r: r[1], reverse=True)
        lines = ["💞 你的亲密度排行"]
        lines += [
            f"· {self.store.display_name(key, o)} — {p}（{games_extra.relation_stage(p)}）"
            for o, p in rows[:8]
        ]
        return event.plain_result("\n".join(lines))

    @filter.command("表白", alias={"confess"})
    async def cmd_confess(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """向群友表白。

        Args:
            args: ``@某人``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("social"):
            return self._deny("社交玩法已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        target = self._resolve_uid(self._args(event))
        if not target or target == uid:
            return event.plain_result("用法：表白 @某人")
        user = self.store.get_user(key, uid)
        user["name"] = name
        user["confess_count"] = int(user.get("confess_count", 0) or 0) + 1
        per = max(3, self._int("social", "intimacy_per_interact", default=2) * 3)
        bucket = user.setdefault("intimacy", {})
        points, stage = games_extra.add_intimacy(bucket, uid, target, per)
        games.bump_daily(user, "confess")
        await self.store.save(key, force=True)
        other = self.store.display_name(key, target)
        tail = (
            "对方没回应，但空气里已经有心跳声。"
            if points < 100
            else "亲密度已达标，稳了！"
        )
        return event.plain_result(
            f"💌 {games_extra.confess_line(name, other, self._rng)}\n"
            f"（亲密度现为 {points}，{stage}。{tail}）"
        )

    @filter.command("pk", alias={"对决", "决斗", "duel"})
    async def cmd_duel(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """与群友进行积分 PK。

        Args:
            args: ``<@某人> [赌注]``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("social"):
            return self._deny("社交玩法已关闭。")
        parts = self._args(event).split()
        if not parts:
            return event.plain_result("用法：pk @某人 [赌注]，例如：pk 10001 50")
        key = self._session_key(event)
        uid, name = self._sender(event)
        target = self._resolve_uid(parts[0])
        if not target or target == uid:
            return event.plain_result("请 @ 一位群友进行对决（不能和自己打）。")
        default_stake = max(0, self._int("social", "duel_stake", default=20))
        stake = (
            games.parse_amount(parts[1], default_stake)
            if len(parts) > 1
            else default_stake
        )
        stake = max(0, min(stake, 100000))
        a = self.store.get_user(key, uid)
        a["name"] = name
        b = self.store.get_user(key, target)
        if stake > 0 and (int(a["balance"]) < stake or int(b["balance"]) < stake):
            return event.plain_result(f"双方都需要至少 {stake} {self._unit} 才能开打。")
        winner, diff, flavor = games_extra.duel(a, b, self._rng)
        other = self.store.display_name(key, target)
        if winner == "a":
            a["duel_win"] = int(a.get("duel_win", 0) or 0) + 1
            a["balance"] = int(a["balance"]) + stake
            b["balance"] = max(0, int(b["balance"]) - stake)
            line = f"⚔️ {name} 战胜了 {other}（战力差 {diff}）！{flavor}"
        elif winner == "b":
            b["duel_win"] = int(b.get("duel_win", 0) or 0) + 1
            b["balance"] = int(b["balance"]) + stake
            a["balance"] = max(0, int(a["balance"]) - stake)
            line = f"⚔️ {other} 战胜了 {name}（战力差 {diff}）！{flavor}"
        else:
            line = f"⚔️ {name} 与 {other} 打成平手（战力差 {diff}）！{flavor}"
        if stake > 0 and winner != "draw":
            line += f"\n赌注 {stake} {self._unit} 已结算。"
        per = max(1, self._int("social", "intimacy_per_interact", default=2))
        bucket = a.setdefault("intimacy", {})
        points, stage = games_extra.add_intimacy(bucket, uid, target, per)
        games.bump_daily(a, "duel")
        await self.store.save(key, force=True)
        return event.plain_result(f"{line}\n不打不相识，亲密度 {points}（{stage}）。")

    # ------------------------------------------------------------------ 扩展玩法：小工具

    @filter.command("随机", alias={"random", "帮我选"})
    async def cmd_random(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """在多个选项中随机选择。

        Args:
            args: ``选项1|选项2|...`` 或空格分隔。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        raw = self._args(event).strip()
        if not raw:
            return event.plain_result("用法：随机 火锅|烧烤|日料")
        options = [o.strip() for o in raw.split("|") if o.strip()]
        if len(options) < 2:
            options = [o for o in raw.split() if o]
        if len(options) < 2:
            return event.plain_result("至少给两个选项呀，例如：随机 火锅|烧烤")
        return event.plain_result(
            f"🎯 我选：{self._rng.choice(options)}\n（从 {len(options)} 个选项中选出）"
        )

    @filter.command("笑话", alias={"joke", "冷笑话"})
    async def cmd_joke(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """来一条冷笑话。"""
        if reason := self._guard(event):
            return self._deny(reason)
        return event.plain_result("😂 " + games_extra.random_joke(self._rng))

    @staticmethod
    def _version() -> str:
        """读取插件版本号（来自包 ``__init__``）。"""
        try:
            from . import __version__

            return __version__
        except Exception:  # noqa: BLE001 - 版本号读取失败不影响主流程
            return "unknown"

    # ------------------------------------------------------------------ 关键词互动

    def _keyword_rules(self) -> list[tuple[str, str, bool]]:
        """编译并缓存关键词规则。

        配置在运行期不会变（AstrBot 改配置会重载插件），但为了防御
        热更新场景，这里用签名做失效判断，只在内容变化时重建。

        Returns:
            ``[(关键词, 回复, 是否精确匹配), ...]``。
        """
        raw = self._cfg("auto_reply", "rules", default=[]) or []
        signature = repr(raw)
        if self._last_rules_signature == signature and self._keyword_cache is not None:
            return self._keyword_cache[1]

        rules: list[tuple[str, str, bool]] = []
        if isinstance(raw, list):
            for rule in raw[:_MAX_KEYWORD_RULES]:
                if not isinstance(rule, dict):
                    continue
                keyword = str(rule.get("keyword", "")).strip()
                reply = str(rule.get("reply", "")).strip()
                if not keyword or not reply:
                    continue
                exact = rule.get("exact", False)
                if isinstance(exact, str):
                    exact = exact.strip().lower() not in {"false", "0", "no", "off", ""}
                rules.append((keyword, reply, bool(exact)))
        self._keyword_version += 1
        self._last_rules_signature = signature
        self._keyword_cache = (self._keyword_version, rules)
        return rules

    # 说明：关键词互动已并入 on_message 统一入口（见「免唤醒总入口」一节），
    # 不再单独注册事件钩子，避免同一条消息被两个钩子重复处理、以及绕开
    # 免唤醒指令优先级的问题。

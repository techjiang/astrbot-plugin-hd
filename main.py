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

from . import games, games_extra, games_plus, games_tags, games_world
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
    # ---- v1.3.0 新增指令与别名：世界线 / 赛季 / 养成 ----
    "讨伐",
    "开boss",
    "群boss",
    "讨伐状态",
    "boss状态",
    "赛季",
    "本季",
    "宠物",
    "养宠",
    "喂食",
    "照料",
    "撸宠",
    "起名",
    "宠物起名",
    "改名",
    "攻击",
    "输出",
    "砍",
    "boss",
    "season",
    "pet",
    "feed",
    "petname",
    "attack",
    # ---- v1.3.0 新增指令与别名：标签体系 ----
    "标签",
    "成就标签",
    "收集",
    "标签搜索",
    "搜标签",
    "tags",
    "collection",
    "tagsearch",
    # ---- v1.2.0 新增指令与别名 ----
    "我的加成",
    "成就列表",
    "数字破解",
    "扫雷盘",
    "井字棋",
    "决斗盘",
    "大转盘",
    "转生",
    "重生",
    "竞猜",
    "预测",
    "开盘",
    "下注",
    "押注",
    "投注",
    "开奖",
    "结算",
    "成就",
    "转盘",
    "扫雷",
    "破解",
    "猜拳",
    "硬币",
    "加成",
    "三连",
    "wager",
    "bet",
    "draw",
    "ttt",
    "tictactoe",
    "mine",
    "minesweeper",
    "codebreaker",
    "mastermind",
    "coin",
    "flip",
    "rps",
    "wheel",
    "rebirth",
    "achieve",
    "achievements",
    "bonus",
    "石头剪刀布",
    # 以下为「分发键必须全部可剥离」的补漏项（见 tests 里的结构断言）：
    # 只要 _build_dispatch 里挂了新触发词，就必须同时加进本表，
    # 否则带参数的指令会把指令名当成第一个参数（历史 bug 类别）。
    "抛硬币",
    "抽奖盘",
    "道具店",
    "成语接龙",
    "扎心话",
    "帮我选",
    "再来一张",
    "不玩了",
    "票数",
    "道具",
    "幸运",
    "汤",
    "hd状态",
    "transfer",
    "voteresult",
    "inventory",
    "qiandao",
    "help",
    "status",
    "8ball",
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
# 对局类游戏的统一 TTL（秒），超时即回收，避免会话状态常驻内存
_BOARD_TTL = 1800
# 弹幕竞猜自动开奖的宽限时间（秒）：到期后先开奖，再保留这段时间供查看结果
_WAGER_GRACE = 600


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
        # 第二批扩展玩法状态（按会话隔离）
        self._wagers: dict[str, games_plus.WagerGame] = {}
        self._tictactoes: dict[str, games_plus.TicTacToeGame] = {}
        self._mines: dict[str, games_plus.MinefieldGame] = {}
        self._codes: dict[str, games_plus.CodebreakerGame] = {}
        self._duels: dict[str, dict[str, Any]] = {}
        # 第三批扩展玩法状态（按会话隔离）
        self._bosses: dict[str, games_world.BossFight] = {}
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

        # 第二批扩展：对局类
        reg(self.cmd_wager, "竞猜", "预测", "开盘", "wager")
        reg(self.cmd_bet, "下注", "押注", "投注", "bet")
        reg(self.cmd_draw, "开奖", "结算", "draw")
        reg(self.cmd_tictactoe, "井字棋", "三连", "tictactoe", "ttt")
        reg(self.cmd_mine, "扫雷", "扫雷盘", "mine", "minesweeper")
        reg(
            self.cmd_codebreaker,
            "破解",
            "数字破解",
            "codebreaker",
            "mastermind",
        )
        reg(self.cmd_coin, "抛硬币", "硬币", "coin", "flip")
        reg(self.cmd_rpsls, "决斗盘", "猜拳", "石头剪刀布", "rps")
        reg(self.cmd_wheel, "转盘", "大转盘", "wheel", "抽奖盘")

        # 第二批扩展：成长类
        reg(self.cmd_achievements, "成就", "成就列表", "achieve", "achievements")
        reg(self.cmd_tags, "标签", "成就标签", "收集", "tags", "collection")
        reg(self.cmd_tag_search, "标签搜索", "搜标签", "tagsearch")

        # 第三批扩展：世界线 / 赛季 / 养成
        reg(self.cmd_boss, "讨伐", "开boss", "boss", "群boss")
        reg(self.cmd_boss_attack, "攻击", "输出", "砍", "attack")
        reg(self.cmd_boss_info, "讨伐状态", "boss状态")
        reg(self.cmd_season, "赛季", "season", "本季")
        reg(self.cmd_pet, "宠物", "pet", "养宠")
        reg(self.cmd_pet_feed, "喂食", "照料", "feed", "撸宠")
        reg(self.cmd_pet_name, "起名", "改名", "petname")
        reg(self.cmd_rebirth, "转生", "重生", "rebirth")
        reg(self.cmd_bonus, "我的加成", "加成", "bonus")

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

        # 同一会话里可能同时存在多种对局（比如猜数字还没结束又开了扫雷）。
        # 消息该归谁，按「输入形态的唯一性」决定，优先级从最具体到最宽泛：
        #
        #   1. 数字破解：长度恰好等于答案位数 —— 只有它认这个形态；
        #   2. 井字棋 / 扫雷：单格号（1~9），按开局时间倒序（新局优先）；
        #   3. 猜数字 / 数字炸弹：任意正整数。
        #
        # 不这么排的话，一个 4 位数字会先被「扫雷」抢走，然后被判成
        # 「格号超范围」而回一句令人困惑的报错。
        if text.isdigit():
            code_game = self._codes.get(key)
            if code_game is not None and len(text) == code_game.digits:
                result = await self._code_submit(event, text)
                if result is not None:
                    event.stop_event()
                    await event.send(result)
                    return True

            if len(text) == 1 and not text.startswith("0"):
                # 新增的对局优先接管，避免被同一会话里更早开的旧局抢走
                boards: list[tuple[float, str, Any]] = []
                if key in self._tictactoes:
                    boards.append(
                        (self._tictactoes[key].started_at, "ttt", self._tictactoes[key])
                    )
                if key in self._mines:
                    boards.append(
                        (self._mines[key].started_at, "mine", self._mines[key])
                    )
                for _ts, kind, _game in sorted(boards, reverse=True):
                    result = (
                        await self._ttt_move(event, int(text))
                        if kind == "ttt"
                        else await self._mine_open(event, int(text))
                    )
                    if result is not None:
                        event.stop_event()
                        await event.send(result)
                        return True

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

        # 竞猜到点自动开奖
        wager = self._wagers.get(key)
        if wager is not None and not wager.settled and wager.expired():
            result = await self._settle_wager(key, wager, 0)
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
                # 对局类状态统一回收，避免长期运行后常驻内存
                self._tictactoes = {
                    k: g
                    for k, g in self._tictactoes.items()
                    if now - g.started_at < _BOARD_TTL
                }
                self._mines = {
                    k: g
                    for k, g in self._mines.items()
                    if now - g.started_at < max(_BOARD_TTL, 600)
                }
                self._codes = {
                    k: g
                    for k, g in self._codes.items()
                    if now - g.started_at < _BOARD_TTL
                }
                self._wagers = {
                    k: g
                    for k, g in self._wagers.items()
                    if now - g.started_at < g.duration + _WAGER_GRACE
                }
                self._bosses = {
                    k: g
                    for k, g in self._bosses.items()
                    if now - g.started_at < _BOARD_TTL
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
        bonus_note = ""
        if ok:
            games.bump_daily(user, "sign")
            times = int(user.get("rebirth", 0) or 0)
            if times:
                extra = games_plus.apply_bonus(reward, times) - reward
                if extra:
                    user["balance"] = int(user["balance"]) + extra
                    bonus_note = f"（转生加成 +{extra}）"
            bonus_note += self._achievement_scan(user)
        await self.store.save(key)
        flavor = self._rng.choice(games.SIGN_FLAVORS)
        if ok:
            return event.plain_result(
                f"@{name} {msg}{bonus_note}\n当前余额 {user['balance']} {self._unit}，"
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
        parts = (args.strip() or self._args(event).strip()).split()
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
                user = self.store.get_user(key, uid)
                user["name"] = name
                reward = games_plus.apply_bonus(
                    self._guess_reward(), int(user.get("rebirth", 0) or 0)
                )
                user["guess_win"] = int(user.get("guess_win", 0) or 0) + 1
                user["balance"] = int(user.get("balance", 0) or 0) + reward
                games.bump_daily(user, "guess")
                hint += f" 奖励 {reward} {self._unit}！{self._achievement_scan(user)}"
                await self.store.save(key, force=True)
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
        arg = args.strip() or self._args(event).strip()
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
        user = self.store.get_user(key, uid)
        user["name"] = name
        reward = games_plus.apply_bonus(
            self._int("word_chain", "reward", default=5),
            int(user.get("rebirth", 0) or 0),
        )
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
        raw = args.strip() or self._args(event).strip()
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
        parts = (args.strip() or self._args(event).strip()).split()
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
        _raw = (args.strip() or self._args(event).strip()).split()
        poll_id = _raw[0] if _raw else ""
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
        parts = (args.strip() or self._args(event).strip()).split()
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
        "井字棋": "ttt_win",
        "ttt": "ttt_win",
        "扫雷": "mine_clear",
        "mine": "mine_clear",
        "破解": "code_win",
        "code": "code_win",
        "竞猜": "wager_win",
        "wager": "wager_win",
        "转盘": "wheel_count",
        "wheel": "wheel_count",
        "抛硬币": "coin_win",
        "coin": "coin_win",
        # ---- v1.3.0 新增维度 ----
        "boss伤害": "boss_damage",
        "boss输出": "boss_damage",
        "boss": "boss_damage",
        "boss击杀": "boss_kill",
        "击杀": "boss_kill",
        "本赛季积分": "season_gain",
        "赛季": "season_gain",
        "season": "season_gain",
        "赛季奖励": "season_reward",
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
        "ttt_win": "井字棋",
        "mine_clear": "扫雷通关",
        "code_win": "数字破解",
        "wager_win": "竞猜猜中",
        "wheel_count": "大转盘",
        "coin_win": "抛硬币",
        # ---- v1.3.0 新增维度 ----
        "boss_damage": "Boss 伤害",
        "boss_kill": "Boss 击杀",
        "season_gain": "本赛季积分",
        "season_reward": "赛季奖励",
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
        _raw = (args.strip() or self._args(event).strip()).split()
        code = _raw[0] if _raw else ""

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
        arg = args.strip() or self._args(event).strip()
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
            "井字棋 → 直接发格号 ｜ 扫雷 [边长 雷数] → 直接发格号\n"
            "破解 [位数] → 直接发数字串 ｜ 猜拳 <手势> [赌注]\n"
            "竞猜 问题 | A | B → 下注 <序号> <金额> → 开奖 [序号]\n"
            "转盘 [次数] ｜ 抛硬币 [正|反] [赌注]\n"
            "\n"
            "【社交】\n"
            "转账 <用户> <数量> ｜ 打劫 <用户> <数量> ｜ 扎心 [@某人]\n"
            "赠送 @某人 奶茶 ｜ 亲密度 [@某人] ｜ 表白 @某人 ｜ pk @某人 [赌注]\n"
            "\n"
            "【商店与排行】\n"
            "商店 → 购买 <道具名> ｜ 背包 ｜ 佩戴 <称号名>\n"
            f"排行榜 <维度>（{dims}）\n"
            "\n"
            "【成长】\n"
            "成就 ｜ 转生 [确认] ｜ 我的加成（含今日折扣）\n"
            "标签 [稀有|分类|已获得] ｜ 标签搜索 <关键词>\n"
            "赛季 → 赛季 领取 ｜ 宠物 → 喂食 / 起名 <名字>\n"
            "\n"
            "【世界线】\n"
            "讨伐 [Boss名] → 攻击 <积分> → 讨伐状态（全群协作，按伤害分配奖池）\n"
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
            games.parse_amount(
                (args.strip() or self._args(event).strip()).split()[0], lo
            )
            if (args.strip() or self._args(event).strip()).split()
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
        nums = [
            int(x)
            for x in (args.strip() or self._args(event).strip()).split()
            if x.isdigit()
        ]
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
        ask = args.strip() or self._args(event).strip()
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
        keyword = args.strip() or self._args(event).strip() or "我最快"
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
        percent = self._discount(event)
        lines = [f"🛒 互动商店（货币：{self._unit}）"]
        if percent:
            lines.append(f"🎉 {games_plus.discount_text(percent)}")
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
                if item_kind != kind:
                    continue
                shown = games_plus.discounted_price(price, percent)
                price_text = (
                    f"{shown} {self._unit}（原价 {price}）"
                    if shown != price
                    else f"{price} {self._unit}"
                )
                lines.append(f"· {name} —— {price_text}（{desc}）")
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
        query = args.strip() or self._args(event).strip()
        if not query:
            return event.plain_result("用法：购买 <道具名>，可先发「商店」查看列表。")
        found = games_extra.resolve_shop_item(query)
        if not found:
            return event.plain_result(
                f"没有找到道具「{query}」，可发「商店」查看列表。"
            )
        item_id, name, base_price, kind, _desc = found
        key = self._session_key(event)
        uid, uname = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = uname
        percent = self._discount(event)
        price = games_plus.discounted_price(base_price, percent)
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
        tail = self._achievement_scan(user)
        await self.store.save(key, force=True)
        saved = base_price - price
        if saved > 0:
            note += f"\n（今日折扣省下 {saved} {self._unit}）"
        return event.plain_result(
            f"✅ 购买成功：{name}，花费 {price} {self._unit}，"
            f"余额 {user['balance']}。{note}{tail}"
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
        target = self._resolve_uid(args.strip() or self._args(event)) or str(
            event.get_sender_id()
        )
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
        title = args.strip() or self._args(event).strip()
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
        parts = (args.strip() or self._args(event).strip()).split()
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
        target = self._resolve_uid(args.strip() or self._args(event))
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
        target = self._resolve_uid(args.strip() or self._args(event))
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
        parts = (args.strip() or self._args(event).strip()).split()
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

    # ------------------------------------------------------------------ 第二批扩展：对局类

    def _achievement_scan(self, user: dict) -> str:
        """检查并解锁成就，顺带把推荐/全局加成需要的字段补齐。

        每次成功结算后调用即可；无新成就时返回空字符串，方便直接拼消息。

        Args:
            user: 用户档案（就地更新）。

        Returns:
            形如 ``"\n🎉 解锁成就 签到七日（+60 互动币）"`` 的提示；无新成就时为 ``""``。
        """
        gained, _total = games_plus.check_achievements(user)
        if not gained:
            return ""
        names = "、".join(f"{name}（+{reward}）" for _code, name, reward in gained)
        return f"\n🎉 解锁成就：{names}"

    def _discount(self, event: AstrMessageEvent) -> int:
        """当前会话的今日商店折扣（百分点）。"""
        if not self._bool("shop", "daily_discount", default=True):
            return 0
        return games_plus.daily_discount(self._session_key(event), games.today_str())

    @filter.command("竞猜", alias={"预测", "开盘", "wager"})
    async def cmd_wager(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """发起一盘弹幕竞猜，用法：竞猜 晚饭吃啥 | 火锅 | 烧烤。

        Args:
            args: ``问题 | 选项1 | 选项2 ...``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("wager"):
            return self._deny("竞猜已关闭。")
        raw = args.strip() or self._args(event).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()] if "|" in raw else []
        if len(parts) < 3:
            return event.plain_result(
                "用法：竞猜 问题 | 选项1 | 选项2，例如：竞猜 晚饭吃啥 | 火锅 | 烧烤"
            )
        question, options = parts[0], parts[1:]
        max_options = max(2, min(8, self._int("wager", "max_options", default=5)))
        if len(options) > max_options:
            return event.plain_result(f"选项最多 {max_options} 个。")
        key = self._session_key(event)
        uid, _ = self._sender(event)
        duration = self._int("wager", "duration_seconds", default=180)
        game = games_plus.WagerGame.new(question, options, uid, duration)
        self._wagers[key] = game
        lines = [
            f"🎲 竞猜开盘：{question}",
            f"编号「{game.duration // 60} 分钟」后自动开奖",
        ]
        lines = [
            f"🎲 竞猜开盘：{question}",
            f"时长 {game.duration // 60} 分钟，到期自动开奖",
        ]
        for i, opt in enumerate(game.options, 1):
            lines.append(f"{i}. {opt}")
        lines.append(
            "发送「下注 <序号> <金额>」参与；发起人发「开奖 <序号>」可提前开奖。"
        )
        return event.plain_result("\n".join(lines))

    @filter.command("下注", alias={"押注", "投注", "bet"})
    async def cmd_bet(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """为当前竞猜下注。

        Args:
            args: ``<选项序号> <金额>``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("wager"):
            return self._deny("竞猜已关闭。")
        raw = (args.strip() or self._args(event).strip()).split()
        if len(raw) < 2 or not raw[0].isdigit():
            return event.plain_result("用法：下注 <选项序号> <金额>，例如：下注 1 50")
        key = self._session_key(event)
        game = self._wagers.get(key)
        if game is None:
            return event.plain_result(
                "当前没有进行中的竞猜，发送「竞猜 问题 | A | B」开一盘。"
            )
        uid, name = self._sender(event)
        index = int(raw[0])
        amount = games.parse_amount(raw[1], default=-1)
        if amount <= 0:
            return event.plain_result("下注金额必须是不小于 1 的整数。")
        cap = max(1, self._int("wager", "max_bet", default=1000))
        user = self.store.get_user(key, uid)
        user["name"] = name
        old_index, old_amount = game.bets.get(uid, (0, 0))
        # 可用余额 = 当前余额 + 旧注（改注时旧注会全额退回，可重复使用）
        available = int(user.get("balance", 0) or 0) + old_amount
        # 关键：超限/超余额一律**报错**而不是静默收敛。静默收敛会让玩家
        # 以为自己只押了 1，实际押了 1000（或反之），是纠纷之源。
        if amount > cap:
            return event.plain_result(
                f"单次下注上限为 {cap} {self._unit}，请调小金额。"
            )
        if available < amount:
            return event.plain_result(
                f"余额不足，下注 {amount} 需要 {amount} {self._unit}，"
                f"你当前可用 {available}。"
            )
        index_ok = 1 <= index <= len(game.options)
        if not index_ok:
            return event.plain_result(f"选项序号应为 1 ~ {len(game.options)}。")
        ok, msg = game.place(uid, index, amount)
        if not ok:
            return event.plain_result(msg)
        delta = int(msg.split(":", 1)[1])
        user["balance"] = max(0, int(user.get("balance", 0) or 0) + delta)
        user["wager_total"] = int(user.get("wager_total", 0) or 0) + 1
        await self.store.save(key)
        odds = game.odds().get(game.options[index - 1], 0.0)
        return event.plain_result(
            f"✅ @{name} 下注「{game.options[index - 1]}」{amount} {self._unit}"
            f"（当前赔率 ×{odds}）\n总池 {game.total_pool()}，余额 {user['balance']}。"
        )

    @filter.command("开奖", alias={"结算", "draw"})
    async def cmd_draw(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """为当前竞猜开奖；不填序号则随机开奖。

        Args:
            args: 可选正确选项序号。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        game = self._wagers.get(key)
        if game is None:
            return event.plain_result("当前没有进行中的竞猜。")
        raw = (args.strip() or self._args(event).strip()).split()
        answer = int(raw[0]) if raw and raw[0].isdigit() else 0
        return await self._settle_wager(key, game, answer)

    async def _settle_wager(
        self, key: str, game: games_plus.WagerGame, answer: int
    ) -> MessageEventResult:
        """结算一盘竞猜并按赔率派彩。

        Args:
            key: 会话标识。
            game: 竞猜状态。
            answer: 正确选项序号；``0`` 表示随机。

        Returns:
            结算消息。
        """
        self._wagers.pop(key, None)
        picked = game.settle(answer, self._rng)
        option = game.options[picked - 1]

        # 零和结算：奖金**全部**来自输家押注的本金，中奖者之间按注额比例分配。
        # 下注时本金已从各人余额扣除，所以这里：
        #   中奖者 余额 += 本金 + 奖金份额
        #   输家   不再动（本金就是奖金来源）
        # 无论下注怎么分布，sum(余额变化) = -输家池 ≤ 0，不会凭空造分。
        losses = game.total_pool() - game.option_pool.get(option, 0)
        winner_pool = game.option_pool.get(option, 0)
        odds = game.odds().get(option, 1.0)
        winners: list[str] = []
        for uid, (index, amount) in game.bets.items():
            if index != picked:
                continue
            user = self.store.get_user(key, uid)
            _back, share = games_plus.wager_payout(
                amount, odds, losers_pool=losses, winner_pool=winner_pool
            )
            user["balance"] = int(user.get("balance", 0) or 0) + amount + share
            user["wager_win"] = int(user.get("wager_win", 0) or 0) + 1
            sign = f"+{share}" if share > 0 else "±0"
            winners.append(f"{self.store.display_name(key, uid)} {sign}")
        # 开奖本身也算一次成就检查的时机（积累型成就不走这里，但成本极低）
        await self.store.save(key, force=True)
        head = f"🎯 开奖！正确答案是「{option}」（赔率 ×{odds}）"
        if winners:
            tail = "｜".join(winners)
            if losses <= 0:
                tail += "\n大家都押中了同一个选项，没有输家，所以只退还本金。"
        else:
            tail = "这一轮没人押中，池子里的积分留作下一局的好运。"
        return self._deny(f"{head}\n{tail}")

    @filter.command("井字棋", alias={"三连", "tictactoe", "ttt"})
    async def cmd_tictactoe(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """和 AI 下一盘井字棋，直接发格号落子。

        Args:
            args: 可选首步格号（1~9）。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("tictactoe"):
            return self._deny("井字棋已关闭。")
        key = self._session_key(event)
        game = games_plus.TicTacToeGame.new()
        head = "⭕ #井字棋 开始了，你是 X，直接发格号（1~9）落子。"
        raw = (args.strip() or self._args(event).strip()).split()
        if raw and raw[0].isdigit():
            first = int(raw[0]) - 1
            result, move = game.play(first, self._rng)
            if result != "invalid":
                if not game.finished:
                    self._tictactoes[key] = game
                return event.plain_result(
                    f"{head}\n{game.render()}\n"
                    f"你落在 {first + 1}，我落在 {move + 1}。{self._ttt_tail(result, game)}"
                )
        self._tictactoes[key] = game
        return event.plain_result(f"{head}\n{game.render()}")

    @staticmethod
    def _ttt_tail(result: str, game: games_plus.TicTacToeGame) -> str:
        """井字棋落子结果文案。"""
        if result == "win":
            return "🎉 你赢了！"
        if result == "lose":
            return "🤖 我赢了，再来一局？"
        if result == "draw":
            return "🤝 平局，棋逢对手。"
        return "轮到你了。"

    async def _ttt_move(
        self, event: AstrMessageEvent, cell: int
    ) -> MessageEventResult | None:
        """处理井字棋落子（供自动接管复用）。

        Args:
            event: 消息事件。
            cell: 格号（1~9）。

        Returns:
            结果；无进行中的对局时返回 ``None``。
        """
        if not self._feature_on("tictactoe"):
            return None
        key = self._session_key(event)
        game = self._tictactoes.get(key)
        if game is None or game.finished:
            return None
        if time.time() - game.started_at > _BOARD_TTL:
            self._tictactoes.pop(key, None)
            return None
        uid, name = self._sender(event)
        result, move = game.play(cell - 1, self._rng)
        if result == "invalid":
            return None
        reward = max(0, self._int("tictactoe", "reward", default=15))
        tail_extra = ""
        if game.finished:
            self._tictactoes.pop(key, None)
            user = self.store.get_user(key, uid)
            user["name"] = name
            if result == "win":
                user["rebirth"] = int(user.get("rebirth", 0) or 0)
                gain = games_plus.apply_bonus(reward, user["rebirth"])
                user["balance"] = int(user.get("balance", 0) or 0) + gain
                user["ttt_win"] = int(user.get("ttt_win", 0) or 0) + 1
                tail_extra = f"\n+{gain} {self._unit}"
            elif result == "lose":
                punish = max(0, self._int("tictactoe", "punish", default=0))
                if punish:
                    user["balance"] = max(0, int(user.get("balance", 0) or 0) - punish)
                    tail_extra = f"\n-{punish} {self._unit}"
            tail_extra += self._achievement_scan(user)
            await self.store.save(key, force=True)
        return event.plain_result(
            f"{game.render()}\n你落在 {cell}，我落在 {move + 1}。"
            f"{self._ttt_tail(result, game)}{tail_extra}"
        )

    @filter.command("扫雷", alias={"扫雷盘", "mine", "minesweeper"})
    async def cmd_mine(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开一盘多人共建的扫雷，直接发格号翻格。

        Args:
            args: 可选 ``边长 雷数``。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("mine"):
            return self._deny("扫雷已关闭。")
        key = self._session_key(event)
        nums = [
            int(x)
            for x in (args.strip() or self._args(event).strip()).split()
            if x.isdigit()
        ]
        size = nums[0] if nums else self._int("mine", "size", default=6)
        mines = nums[1] if len(nums) > 1 else self._int("mine", "mines", default=6)
        game = games_plus.MinefieldGame.new(size, mines, self._rng)
        self._mines[key] = game
        return event.plain_result(
            f"💣 扫雷盘已生成（{game.size}×{game.size}，{game.mines} 颗雷）。\n"
            f"{game.render()}\n"
            f"直接发格号（1~{game.size * game.size}）翻格，踩雷者扣分，"
            f"全部清完大家分奖池。"
        )

    async def _mine_open(
        self, event: AstrMessageEvent, cell: int
    ) -> MessageEventResult | None:
        """翻一格扫雷（供自动接管复用）。

        Args:
            event: 消息事件。
            cell: 格号（1 起）。

        Returns:
            结果；无进行中的对局时返回 ``None``。
        """
        if not self._feature_on("mine"):
            return None
        key = self._session_key(event)
        game = self._mines.get(key)
        if game is None or game.cleared():
            return None
        timeout = self._int("mine", "timeout_seconds", default=600)
        if time.time() - game.started_at > timeout:
            self._mines.pop(key, None)
            return None
        uid, name = self._sender(event)
        boom, msg, opened = game.open(cell - 1)
        user = self.store.get_user(key, uid)
        user["name"] = name
        if not opened:
            return event.plain_result(f"@{name} {msg}")
        if boom:
            self._mines.pop(key, None)
            punish = max(0, self._int("mine", "punish", default=20))
            user["balance"] = max(0, int(user.get("balance", 0) or 0) - punish)
            tail = self._achievement_scan(user)
            await self.store.save(key, force=True)
            return event.plain_result(
                f"{msg}\n@{name} 踩雷，扣 {punish} {self._unit}"
                f"（余额 {user['balance']}）。{tail}"
            )
        per = max(0, self._int("mine", "reward_per_cell", default=1))
        gain = len(opened) * per
        gain = games_plus.apply_bonus(gain, int(user.get("rebirth", 0) or 0))
        user["balance"] = int(user.get("balance", 0) or 0) + gain
        user["mine_open"] = int(user.get("mine_open", 0) or 0) + len(opened)
        tail = ""
        if game.cleared():
            bonus = max(0, self._int("mine", "clear_reward", default=80))
            bonus = games_plus.apply_bonus(bonus, int(user.get("rebirth", 0) or 0))
            user["balance"] = int(user.get("balance", 0) or 0) + bonus
            user["mine_clear"] = int(user.get("mine_clear", 0) or 0) + 1
            self._mines.pop(key, None)
            tail = f"\n🧹 通关奖励 {bonus} {self._unit}！"
        tail += self._achievement_scan(user)
        await self.store.save(key, force=True)
        return event.plain_result(
            f"@{name} {msg}（+{gain} {self._unit}）\n{game.render()}{tail}"
        )

    @filter.command("破解", alias={"猜数字密码", "codebreaker", "mastermind"})
    async def cmd_codebreaker(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开一局数字破解（Mastermind 规则），直接发数字串作答。

        Args:
            args: 可选猜测数字串。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("codebreaker"):
            return self._deny("数字破解已关闭。")
        key = self._session_key(event)
        game = games_plus.CodebreakerGame.new(
            self._int("codebreaker", "digits", default=games_plus.CODEBREAKER_DIGITS),
            self._int("codebreaker", "max_attempts", default=8),
            self._rng,
        )
        self._codes[key] = game
        return event.plain_result(
            f"🔐 数字破解开始！答案是 {game.digits} 位数字，"
            f"共 {game.max_attempts} 次机会。\n直接发数字串作答，"
            f"我会告诉你「位置正确」与「数字正确但位置不对」的个数。"
        )

    async def _code_submit(
        self, event: AstrMessageEvent, guess: str
    ) -> MessageEventResult | None:
        """提交一次数字破解（供自动接管复用）。

        Args:
            event: 消息事件。
            guess: 猜测的数字串。

        Returns:
            结果；不匹配或没有对局时返回 ``None``。
        """
        if not self._feature_on("codebreaker"):
            return None
        key = self._session_key(event)
        game = self._codes.get(key)
        if game is None:
            return None
        if time.time() - game.started_at > _BOARD_TTL:
            self._codes.pop(key, None)
            return None
        digits = "".join(ch for ch in guess if ch.isdigit())
        if len(digits) != game.digits:
            return None
        uid, name = self._sender(event)
        result, msg, exact, misplaced = game.submit(digits)
        if result == "invalid":
            return None
        tail = ""
        if result in ("win", "lose"):
            self._codes.pop(key, None)
            user = self.store.get_user(key, uid)
            user["name"] = name
            if result == "win":
                reward = max(0, self._int("codebreaker", "reward", default=120))
                gain = games_plus.apply_bonus(reward, int(user.get("rebirth", 0) or 0))
                user["balance"] = int(user.get("balance", 0) or 0) + gain
                user["code_win"] = int(user.get("code_win", 0) or 0) + 1
                extra = max(0, game.max_attempts - game.attempts)
                spot_extra = extra * max(
                    0, self._int("codebreaker", "early_bonus", default=10)
                )
                if spot_extra:
                    user["balance"] = int(user["balance"]) + spot_extra
                tail = f" 奖励 {gain} {self._unit}"
                if spot_extra:
                    tail += f"（提前 {extra} 次完成，额外 +{spot_extra}）"
                tail += "！"
            tail += self._achievement_scan(user)
            await self.store.save(key, force=True)
        return event.plain_result(f"@{name} {msg}{tail}")

    @filter.command("抛硬币", alias={"硬币", "coin", "flip"})
    async def cmd_coin(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """抛硬币猜正反。

        Args:
            args: ``正`` / ``反``，可跟赌注。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("coin"):
            return self._deny("抛硬币已关闭。")
        raw = (args.strip() or self._args(event).strip()).split()
        key = self._session_key(event)
        uid, name = self._sender(event)
        if not raw:
            side = games_plus.flip_coin(self._rng)
            return event.plain_result(
                f"🪙 硬币落下：{side}！（想猜的话发「抛硬币 正 50」）"
            )
        pick = games_plus.resolve_coin_bet(raw[0])
        if not pick:
            return event.plain_result("用法：抛硬币 [正|反] [赌注]，例如：抛硬币 正 50")
        bet = games.parse_amount(raw[1], default=0) if len(raw) > 1 else 0
        bet = max(0, min(bet, max(1, self._int("coin", "max_bet", default=500))))
        user = self.store.get_user(key, uid)
        user["name"] = name
        if bet and int(user["balance"]) < bet:
            return event.plain_result(
                f"余额不足，赌注 {bet} {self._unit}，你只有 {user['balance']}。"
            )
        side = games_plus.flip_coin(self._rng)
        win = side == pick
        if bet:
            user["balance"] = (
                int(user["balance"]) + bet
                if win
                else max(0, int(user["balance"]) - bet)
            )
        tail = ""
        if win:
            user["coin_win"] = int(user.get("coin_win", 0) or 0) + 1
            tail = self._achievement_scan(user)
        await self.store.save(key, force=True) if bet or tail else None
        result = "🎉 猜中了！" if win else "😅 猜错了。"
        money = f"（{'+' if win else '-'}{bet} {self._unit}）" if bet else ""
        return event.plain_result(
            f"🪙 @{name} 你猜 {pick}，硬币是 {side}。{result}{money}"
            f"\n余额 {user['balance']} {self._unit}。{tail}"
        )

    @filter.command("决斗盘", alias={"猜拳", "rps", "石头剪刀布"})
    async def cmd_rpsls(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """发起一场石头剪刀布蜥蜴斯波克对决。

        Args:
            args: ``石头|剪刀|布|蜥蜴|斯波克``，可跟赌注。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("rpsls"):
            return self._deny("决斗盘已关闭。")
        raw = (args.strip() or self._args(event).strip()).split()
        if not raw:
            moves = "、".join(games_plus.RPSLS_BEATS)
            return event.plain_result(
                f"✊ 用法：决斗盘 <手势> [赌注]\n可选手势：{moves}\n"
                f"规则：石头砸剪刀、剪刀剪布、布包石头、石头压蜥蜴、"
                f"蜥蜴毒斯波克、斯波克蒸发石头、斯波克钝化剪刀、蜥蜴吃布、布推翻斯波克、剪刀斩蜥蜴。"
            )
        mine = games_plus.resolve_rpsls(raw[0])
        if not mine:
            return event.plain_result(
                "没有识别到这个手势，可选：" + "、".join(games_plus.RPSLS_BEATS)
            )
        key = self._session_key(event)
        uid, name = self._sender(event)
        bet = games.parse_amount(raw[1], default=0) if len(raw) > 1 else 0
        bet = max(0, min(bet, max(1, self._int("rpsls", "max_bet", default=500))))
        user = self.store.get_user(key, uid)
        user["name"] = name
        if bet and int(user["balance"]) < bet:
            return event.plain_result(
                f"余额不足，赌注 {bet} {self._unit}，你只有 {user['balance']}。"
            )
        foe = self._rng.choice(list(games_plus.RPSLS_BEATS))
        verdict = games_plus.rpsls_judge(mine, foe)
        if verdict == "draw":
            fee = max(0, self._int("rpsls", "draw_fee", default=0))
            text = f"🤝 你出 {mine}，我出 {foe}，平局！"
            if fee:
                user["balance"] = max(0, int(user["balance"]) - fee)
                text += f"（平局手续费 {fee} {self._unit}）"
        elif verdict == "a":
            gain = games_plus.apply_bonus(bet, int(user.get("rebirth", 0) or 0))
            user["balance"] = int(user["balance"]) + gain
            text = f"🎉 你出 {mine}，我出 {foe} —— {games_plus.rpsls_flavor(mine, foe)}，你赢了！"
            if bet:
                text += f"（+{gain} {self._unit}）"
        else:
            user["balance"] = max(0, int(user["balance"]) - bet)
            text = f"😵 你出 {mine}，我出 {foe} —— {games_plus.rpsls_flavor(foe, mine)}，你输了。"
            if bet:
                text += f"（-{bet} {self._unit}）"
        tail = self._achievement_scan(user)
        await self.store.save(key, force=True)
        return event.plain_result(
            f"✊ @{name} {text}\n余额 {user['balance']} {self._unit}。{tail}"
        )

    @filter.command("转盘", alias={"大转盘", "wheel", "抽奖盘"})
    async def cmd_wheel(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """转动幸运大转盘。

        Args:
            args: 可选连抽次数。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("wheel"):
            return self._deny("大转盘已关闭。")
        raw = (args.strip() or self._args(event).strip()).split()
        times = games.parse_amount(raw[0], default=1) if raw else 1
        times = max(1, min(times, max(1, self._int("wheel", "max_spins", default=10))))
        key = self._session_key(event)
        uid, name = self._sender(event)
        cost = max(0, self._int("wheel", "cost", default=50))
        cooldown = max(0, self._int("wheel", "cooldown_seconds", default=10))
        user = self.store.get_user(key, uid)
        user["name"] = name
        now = time.time()
        last = float(user.get("last_wheel_ts", 0) or 0)
        if cooldown and now - last < cooldown:
            return event.plain_result(
                f"转盘冷却中，还需 {int(cooldown - (now - last)) + 1} 秒。"
            )
        total_cost = cost * times
        if int(user["balance"]) < total_cost:
            return event.plain_result(
                f"余额不足，{times} 连抽需要 {total_cost} {self._unit}，"
                f"你只有 {user['balance']}。"
            )
        user["balance"] = int(user["balance"]) - total_cost
        lines = [f"🎡 @{name} 大转盘 {times} 连抽（消耗 {total_cost} {self._unit}）"]
        net = -total_cost
        for _ in range(times):
            kind, delta, label = games_plus.wheel_spin(cost, self._rng)
            net += delta + cost
            lines.append(f"· {label}")
            if kind == "item":
                titles = user.setdefault("titles", [])
                if label not in titles:
                    titles.append(label)
                    user["title"] = label
        bonus = games_plus.apply_bonus(max(0, net), int(user.get("rebirth", 0) or 0))
        gained = bonus if net > 0 else max(0, net)
        user["balance"] = int(user["balance"]) + gained
        user["wheel_count"] = int(user.get("wheel_count", 0) or 0) + times
        user["last_wheel_ts"] = now
        games.bump_daily(user, "wheel")
        tail = self._achievement_scan(user)
        await self.store.save(key, force=True)
        return event.plain_result(
            "\n".join(lines)
            + f"\n本次净变动 {gained - total_cost:+d}，余额 {user['balance']} {self._unit}。{tail}"
        )

    # ------------------------------------------------------------------ 第二批扩展：成长类

    @filter.command("成就", alias={"成就列表", "achieve", "achievements"})
    async def cmd_achievements(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看成就进度。

        Args:
            args: 可选 @某人。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        target = self._resolve_uid(args.strip() or self._args(event)) or str(
            event.get_sender_id()
        )
        name = self.store.display_name(key, target)
        entries = self._tag_entries(key, target)
        info = games_tags.summarize(entries)
        lines = [
            f"🏅 {name} 的成就（{info['unlocked']}/{info['total']}）",
            games_tags.render_summary(entries),
            "",
        ]
        for entry in entries:
            mark = "✅" if entry.unlocked else "⏳"
            badge = games_tags.rarity_badge(entry.rarity)
            if entry.unlocked:
                lines.append(f"{mark}{badge} {entry.name}（+{entry.reward}）")
            else:
                lines.append(
                    f"{mark}{badge} {entry.name}（{entry.current}/{entry.threshold}）"
                )
        lines.append("")
        lines.append("按稀有度/分类筛选：标签 稀有｜标签 日常｜标签 搜索 抽奖")
        return event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ 标签体系（v1.3.0）

    def _tag_entries(self, key: str, uid: str) -> list[games_tags.TagEntry]:
        """构造某个用户的标签条目列表。

        Args:
            key: 会话标识。
            uid: 用户 ID。

        Returns:
            标签条目列表（分类与稀有度已补齐）。
        """
        user = self.store.get_user(key, uid)
        return games_tags.build_entries(
            games_plus.ACHIEVEMENTS,
            user,
            categories=games_plus.ACHIEVEMENT_CATEGORIES,
            unlocked_at=games_plus.achievement_unlocked_at(user),
        )

    @filter.command("标签", alias={"成就标签", "收集", "tags", "collection"})
    async def cmd_tags(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看标签（成就徽章）收集情况，支持筛选与排序。

        用法：
            标签                     —— 概览 + 未完成清单
            标签 稀有                —— 只看某个稀有度
            标签 日常                —— 只看某个分类
            标签 已获得              —— 只看已解锁
            标签 搜索 签到           —— 关键词搜索
            标签 稀有 搜索 抽奖      —— 组合筛选（顺序不限）
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        raw = (args.strip() or self._args(event).strip()).strip()
        target = self._resolve_uid(raw) or str(event.get_sender_id())
        name = self.store.display_name(key, target)
        entries = self._tag_entries(key, target)

        query, category, rarity, only_locked, only_unlocked = self._parse_tag_args(raw)
        shown = games_tags.filter_entries(
            entries,
            query=query,
            category=category,
            rarity=rarity,
            only_locked=only_locked,
            only_unlocked=only_unlocked,
        )

        head = f"🏷️ @{name} 的标签收集"
        if not (query or category or rarity or only_locked or only_unlocked):
            lines = [head, games_tags.render_summary(entries), ""]
            pending = games_tags.filter_entries(entries, only_locked=True)
            pending = games_tags.sort_entries(pending, by="progress", descending=False)
            lines.append("📌 最接近完成：")
            lines.append(games_tags.render_list(pending, limit=5))
            lines.append("")
            lines.append("筛选：标签 稀有｜标签 日常｜标签 已获得｜标签 搜索 关键词")
            return event.plain_result("\n".join(lines))

        filter_desc = self._describe_tag_filter(query, category, rarity, only_unlocked)
        lines = [f"{head} · {filter_desc}", games_tags.render_list(shown, limit=20)]
        return event.plain_result("\n".join(lines))

    @filter.command("标签搜索", alias={"搜标签", "tagsearch"})
    async def cmd_tag_search(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """按关键词搜索标签，等价于「标签 搜索 <关键词>」。

        Args:
            args: 关键词。
        """
        raw = args.strip() or self._args(event).strip()
        if not raw:
            return event.plain_result(
                "用法：标签搜索 <关键词>，例如「标签搜索 抽奖」。"
                "也可以直接「标签 <分类|稀有度>」筛选。"
            )
        return await self.cmd_tags(event, f"搜索 {raw}")

    def _parse_tag_args(self, raw: str) -> tuple[str, str, str, bool, bool]:
        """解析标签指令的参数。

        参数是自由词序的 —— 用户既可能写「标签 稀有 搜索 抽奖」，也可能写
        「标签 搜索 抽奖 稀有」。所以这里逐词判定类别，而不是按位置取参。
        「搜索」之后的所有词都归入关键词。

        Args:
            raw: 原始参数文本。

        Returns:
            ``(关键词, 分类, 稀有度, 只看未获得, 只看已获得)``。
        """
        tokens = (raw or "").split()
        query_parts: list[str] = []
        category = ""
        rarity = ""
        only_locked = False
        only_unlocked = False
        i = 0
        while i < len(tokens):
            token = tokens[i]
            norm = games_tags.normalize_query(token)
            if norm in ("搜索", "search", "查"):
                # 「搜索」之后的内容统一作为关键词，保留原始空白
                query_parts.extend(tokens[i + 1 :])
                break
            if norm in ("已获得", "已解锁", "获得", "解锁", "unlocked"):
                only_unlocked = True
            elif norm in ("未获得", "未解锁", "未完成", "locked"):
                only_locked = True
            elif games_tags.is_category(norm):
                # 统一存「码」而不是「名」，后续展示与筛选都按码处理
                category = games_tags.resolve_category(norm)
            elif games_tags.is_rarity(norm):
                rarity = games_tags.resolve_rarity(norm)
            else:
                query_parts.append(token)
            i += 1
        return " ".join(query_parts), category, rarity, only_locked, only_unlocked

    def _describe_tag_filter(
        self,
        query: str,
        category: str,
        rarity: str,
        only_unlocked: bool,
    ) -> str:
        """把筛选条件拼成人类可读的说明。

        Args:
            query: 关键词。
            category: 分类码。
            rarity: 稀有度码。
            only_unlocked: 是否只看已获得。

        Returns:
            筛选说明文本。
        """
        parts: list[str] = []
        if query:
            # 展示归一化后的词：用户写的是「签到！」，提示里不该带着标点
            parts.append(f"关键词「{games_tags.normalize_query(query)}」")
        if category:
            parts.append(games_tags.category_name(category))
        if rarity:
            parts.append(games_tags.rarity_name(rarity))
        if only_unlocked:
            parts.append("已获得")
        return " / ".join(parts) if parts else "全部"

    @filter.command("转生", alias={"重生", "rebirth", "reset等级"})
    async def cmd_rebirth(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """转生：重置积分换取永久收益加成。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("rebirth_play", default=True):
            return self._deny("转生功能已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        if (args.strip() or self._args(event).strip()) not in ("确认", "confirm"):
            times = int(user.get("rebirth", 0) or 0)
            level, _i, _n, _t = games.level_of(user)
            return event.plain_result(
                f"🔄 @{name} 转生说明\n"
                f"当前等级 Lv.{level}｜已转生 {times} 次｜"
                f"当前加成 ×{games_plus.rebirth_bonus(times):.2f}\n"
                f"条件：Lv.{games_plus.REBIRTH_MIN_LEVEL} 以上（上限 {games_plus.REBIRTH_MAX} 次）\n"
                f"代价：积分归零、称号卸下；统计与成就全部保留。\n"
                f"确认后发送「转生 确认」。"
            )
        ok, msg = games_plus.rebirth_reset(user)
        if ok:
            await self.store.save(key, force=True)
        return event.plain_result(f"@{name} {msg}")

    @filter.command("我的加成", alias={"加成", "bonus"})
    async def cmd_bonus(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看转生加成与今日折扣。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        times = int(user.get("rebirth", 0) or 0)
        percent = self._discount(event)
        info = games_tags.summarize(self._tag_entries(key, uid))
        pet_code, pet_name, pet_bonus = games_world.pet_stage(
            games_world.PetState.from_dict(user.get("pet")).care
        )
        season = games_world.season_of(games.today_str())
        self._season_sync(user, season)
        gain = int(user.get("season_gain", 0) or 0)
        stage, _next, _remain = games_world.season_progress(gain)
        return event.plain_result(
            f"⚡ @{name} 的加成\n"
            f"转生 {times} 次｜收益加成 ×{games_plus.rebirth_bonus(times):.2f}\n"
            f"标签 {info['unlocked']}/{info['total']}（{info['percent']}%）\n"
            f"赛季 {season}｜段位 {stage}｜本季增量 {gain}\n"
            f"宠物 {pet_name}（产出 +{pet_bonus}）\n"
            f"今日折扣：{games_plus.discount_text(percent)}"
        )

    # ------------------------------------------------------------------ 第三批扩展：世界线 / 赛季 / 养成（v1.3.0）

    @filter.command("讨伐", alias={"开boss", "boss", "群boss"})
    async def cmd_boss(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """开一局群 Boss 战，用法：讨伐 [boss名]。

        Args:
            args: 可选 Boss 名（史莱姆王 / 烈焰巨龙 / 深海巨妖 / 虚空利维坦）。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("boss", default=True):
            return self._deny("群 Boss 战已关闭。")
        key = self._session_key(event)
        ongoing = self._bosses.get(key)
        if ongoing is not None and not ongoing.finished:
            lines = [
                f"⚠️ 本群已在讨伐「{ongoing.name}」，先打完这一局。",
                ongoing.render(),
            ]
            return event.plain_result("\n".join(lines))

        want = (args.strip() or self._args(event).strip()).strip()
        code = self._match_boss(want)
        if want and code is None:
            names = " / ".join(name for _c, name, *_r in games_world.BOSSES)
            return event.plain_result(f"没有这个 Boss。可选：{names}")

        fight = games_world.BossFight.create(
            code or games_world.BOSSES[0][0], int(time.time())
        )
        self._bosses[key] = fight
        _name, _hp, cost, pool, desc = games_world.BOSS_MAP[fight.code]
        return event.plain_result(
            f"🐲 群 Boss 战开始！\n"
            f"{fight.render()}\n"
            f"{desc}\n"
            f"发送「攻击 <积分>」投入积分造成伤害（每 {cost} 积分 = 1 点伤害）。\n"
            f"击杀后按伤害占比瓜分 {pool} {self._unit} 奖池。"
        )

    @filter.command("攻击", alias={"输出", "砍", "attack"})
    async def cmd_boss_attack(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """对当前 Boss 发起攻击，用法：攻击 <积分>。

        Args:
            args: 投入积分。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        fight = self._bosses.get(key)
        if fight is None or fight.finished:
            return event.plain_result("本群没有进行中的讨伐，先发送「讨伐」开一局。")
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name

        raw = (args.strip() or self._args(event).strip()).split()
        spend = games.parse_amount(raw[0], 0) if raw else 0
        if spend <= 0:
            return event.plain_result("投入积分要大于 0，例如「攻击 100」。")
        cap = max(1, self._int("boss", "max_spend", default=5000))
        if spend > cap:
            return event.plain_result(
                f"单次攻击最多投入 {cap} {self._unit}，别一次梭哈。"
            )
        if spend > int(user.get("balance", 0) or 0):
            return event.plain_result(
                f"积分不足，你只有 {int(user.get('balance', 0) or 0)} {self._unit}。"
            )

        user["balance"] = int(user.get("balance", 0) or 0) - spend
        dealt, remaining, killed = fight.attack(uid, spend)
        if dealt <= 0:
            # 投入不足以折算 1 点伤害：全额退回，不留沉默扣款
            user["balance"] = int(user.get("balance", 0) or 0) + spend
            need = max(1, fight.cost)
            return event.plain_result(f"至少投入 {need} {self._unit} 才能造成伤害。")
        # 溢出伤害要退款：Boss 只剩 10 滴血时你投入 5000，
        # 只该按「实际打出的 10 点」收费，否则最后一下会被当冤大头。
        billed = dealt * max(1, fight.cost)
        refund = spend - billed
        if refund > 0:
            user["balance"] = int(user.get("balance", 0) or 0) + refund
            spend = billed
            fight.spenders[uid] = max(0, fight.spenders.get(uid, 0) - refund)

        user["boss_damage"] = int(user.get("boss_damage", 0) or 0) + dealt
        lines = [f"@{name} 造成 {dealt} 点伤害！"]

        if not killed:
            mine = fight.damages.get(uid, 0)
            lines.append(fight.render())
            lines.append(f"你的累计伤害 {mine}（投入 {fight.spenders.get(uid, 0)}）")
            await self.store.save(key)
            return event.plain_result("\n".join(lines))

        share, killer = fight.settle()
        mine = share.get(uid, 0)
        for target_uid, amount in share.items():
            self.store.add_balance(key, target_uid, amount)
        # 所有参与者都要记功：只给「最后一击」算账会让先出手的人白打工
        for target_uid in fight.damages:
            record = self.store.get_user(key, target_uid)
            record["boss_gold"] = int(record.get("boss_gold", 0) or 0) + share.get(
                target_uid, 0
            )
            if target_uid == killer:
                record["boss_kill"] = int(record.get("boss_kill", 0) or 0) + 1
        self._bosses.pop(key, None)

        # 全员结算后逐人复查成就，避免「只给补刀的人算账」
        tail = self._achievement_scan(self.store.get_user(key, uid))
        lines.append(
            f"💥 {fight.name} 被击败了！最后一击：{self.store.display_name(key, killer)}"
        )
        lines.append(
            f"你分得 {mine} {self._unit}（伤害占比 {fight.damages.get(uid, 0)}/{fight.total_damage}）"
        )
        if tail:
            lines.append(tail.strip())
        await self.store.save(key, force=True)
        return event.plain_result("\n".join(lines))

    @filter.command("讨伐状态", alias={"boss状态"})
    async def cmd_boss_info(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看当前群 Boss 战进度与伤害榜。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        fight = self._bosses.get(key)
        if fight is None:
            names = " / ".join(name for _c, name, *_r in games_world.BOSSES)
            return event.plain_result(f"本群没有进行中的讨伐。\n可选 Boss：{names}")
        lines = [fight.render()]
        if fight.damages:
            rows = sorted(fight.damages.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
            lines.append("📊 伤害榜：")
            for uid, damage in rows:
                lines.append(f"· {self.store.display_name(key, uid)} —— {damage}")
        else:
            lines.append("还没人出手，发送「攻击 <积分>」上啊！")
        return event.plain_result("\n".join(lines))

    @staticmethod
    def _match_boss(text: str) -> str | None:
        """把用户输入的 Boss 名/码匹配到 Boss 码。

        Args:
            text: 用户输入。

        Returns:
            Boss 码；无法匹配返回 ``None``。
        """
        want = "".join((text or "").split()).lower()
        if not want:
            return None
        for code, name, *_rest in games_world.BOSSES:
            if want in (code.lower(), name.lower()):
                return code
        for code, name, *_rest in games_world.BOSSES:
            if want in code.lower() or want in name.lower():
                return code
        return None

    @filter.command("赛季", alias={"season", "本季"})
    async def cmd_season(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """查看本季赛季进度与预计奖励。"""
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        season = games_world.season_of(games.today_str())
        self._season_sync(user, season)
        gain = int(user.get("season_gain", 0) or 0)
        stage, next_stage, remain = games_world.season_progress(gain)
        reward = games_world.season_reward(gain)
        claimed = int(user.get("season_reward", 0) or 0)
        raw = (args.strip() or self._args(event).strip()).strip()

        if raw in ("领取", "领奖", "claim", "领"):
            if reward <= claimed:
                return event.plain_result(
                    f"@{name} 本季（{season}）没有可领的奖励，"
                    f"当前段位「{stage}」已领满（已领 {claimed}）。"
                )
            diff = reward - claimed
            user["season_reward"] = reward
            self.store.add_balance(key, uid, diff)
            self._achievement_scan(user)
            await self.store.save(key, force=True)
            return event.plain_result(
                f"@{name} 领取了「{stage}」段位奖励 {diff} {self._unit}！\n"
                f"当前余额 {int(user.get('balance', 0) or 0)} {self._unit}"
            )

        lines = [
            f"🏆 @{name} 的赛季 · {season}",
            f"本季积分增量 {gain}｜段位 {stage}",
            (
                f"距离「{next_stage}」还差 {remain} 积分"
                if remain
                else "已到最高段位，稳住！"
            ),
            f"结算可得 {reward} {self._unit}（已领 {claimed}）",
        ]
        if reward > claimed:
            lines.append("发送「赛季 领取」立即领取当前段位奖励。")
        elif reward:
            lines.append("本季奖励已领满，继续冲下一段位吧。")
        await self.store.save(key)
        return event.plain_result("\n".join(lines))

    def _season_sync(self, user: dict, season: str) -> None:
        """在赛季切换时重置本季增量。

        只重置「增量」而不动总积分，避免跨月时伤害玩家资产。

        Args:
            user: 用户档案（就地更新）。
            season: 当前赛季标识。
        """
        if str(user.get("season_tag") or "") == season:
            return
        user["season_tag"] = season
        user["season_gain"] = 0
        user["season_reward"] = 0

    @filter.command("宠物", alias={"pet", "养宠", "宠物状态"})
    async def cmd_pet(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """领养 / 查看宠物。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("pet", default=True):
            return self._deny("宠物玩法已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        pet = games_world.PetState.from_dict(user.get("pet"))
        if not pet.born:
            pet.born = int(time.time())
            user["pet"] = pet.to_dict()
            await self.store.save(key)
            return event.plain_result(
                f"@{name} 你领养了一只宠物蛋！\n"
                f"发送「喂食」照料它，或「起名 <名字>」给它取名。"
            )
        now = int(time.time())
        ready = pet.next_feed_at(now)
        lines = [f"@{name} 的宠物", pet.describe()]
        if ready > now:
            wait = games.format_duration(ready - now)
            lines.append(f"⏳ 还要 {wait} 才能再照料。")
        else:
            lines.append("可以喂食啦，发送「喂食」照顾它。")
        return event.plain_result("\n".join(lines))

    @filter.command("喂食", alias={"照料", "feed", "撸宠"})
    async def cmd_pet_feed(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """照料宠物一次（有冷却）。"""
        if reason := self._guard(event):
            return self._deny(reason)
        if not self._feature_on("pet", default=True):
            return self._deny("宠物玩法已关闭。")
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        pet = games_world.PetState.from_dict(user.get("pet"))
        now = int(time.time())
        if not pet.born:
            return event.plain_result("你还没有宠物，先发送「宠物」领养一只。")
        ready = pet.next_feed_at(now)
        if ready > now:
            wait = games.format_duration(ready - now)
            return event.plain_result(f"@{name} 别喂太急啦，还要 {wait} 才能再照料。")

        before_code, before_name, _b = games_world.pet_stage(pet.care)
        pet.care += 1
        pet.fed_at = now
        user["pet"] = pet.to_dict()
        gain = games_world.pet_gain(pet.care, self._rng)
        if gain:
            user["balance"] = int(user.get("balance", 0) or 0) + gain
        after_code, after_name, bonus = games_world.pet_stage(pet.care)
        lines = [
            f"@{name} 照料了「{pet.name or '未命名'}」",
            f"获得 {gain} {self._unit}（当前余额 {int(user.get('balance', 0) or 0)}）",
        ]
        if after_code != before_code:
            lines.append(
                f"🎉 成长啦：{before_name} → {after_name}（每日产出 +{bonus}）"
            )
        else:
            next_name, remain = games_world.pet_need(pet.care)
            if remain:
                lines.append(f"距离「{next_name}」还差 {remain} 次照料")
        await self.store.save(key)
        return event.plain_result("\n".join(lines))

    @filter.command("起名", alias={"宠物起名", "改名", "petname"})
    async def cmd_pet_name(
        self, event: AstrMessageEvent, args: str = ""
    ) -> MessageEventResult | None:
        """给宠物起名，用法：起名 <名字>。

        Args:
            args: 新名字（1~12 字）。
        """
        if reason := self._guard(event):
            return self._deny(reason)
        key = self._session_key(event)
        uid, name = self._sender(event)
        user = self.store.get_user(key, uid)
        user["name"] = name
        pet = games_world.PetState.from_dict(user.get("pet"))
        if not pet.born:
            return event.plain_result("你还没有宠物，先发送「宠物」领养一只。")
        raw = (args.strip() or self._args(event).strip()).strip()
        if not raw:
            return event.plain_result("用法：起名 <名字>，例如「起名 小互动」。")
        if len(raw) > 12:
            return event.plain_result("名字太长了，12 个字以内吧。")
        pet.name = raw
        user["pet"] = pet.to_dict()
        await self.store.save(key)
        return event.plain_result(f"@{name} 宠物已改名为「{raw}」。")

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
        raw = args.strip() or self._args(event).strip()
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

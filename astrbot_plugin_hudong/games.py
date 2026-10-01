"""互动玩法的纯逻辑层。

这里只实现不依赖 AstrBot 运行时的算法与状态机：签到、抽奖、猜数字、接龙。
保持无副作用，便于单测与复用。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

# 抽奖奖池：(权重, 类型, 数值)，类型 amount=积分，type item=虚拟道具/称号
LOTTERY_POOL: list[tuple[int, str, str, int]] = [
    (35, "amount", "谢谢参与，下次一定", 0),
    (25, "amount", "小奖", 5),
    (18, "amount", "中奖", 15),
    (12, "amount", "大奖", 40),
    (6, "amount", "超大奖", 120),
    (3, "item", "限定称号：欧皇", 0),
    (1, "item", "绝版头像框：天选之人", 0),
]

# 猜数字/接龙的正反馈文案，避免回复过于机械
GUESS_HOT_HINTS = ("非常接近了！", "就差一点点！", "感觉很近了！")


def today_str() -> str:
    """返回当前日期字符串（YYYY-MM-DD）。

    Returns:
        当天日期字符串。
    """
    return date.today().isoformat()


def sign_in(
    user: dict,
    min_reward: int,
    max_reward: int,
    streak_bonus: float,
    max_streak_bonus: float,
) -> tuple[bool, int, int, str]:
    """执行一次签到结算。

    Args:
        user: 用户档案（会被就地更新）。
        min_reward: 基础奖励下限。
        max_reward: 基础奖励上限。
        streak_bonus: 连续签到奖励系数。
        max_streak_bonus: 连续奖励倍数上限。

    Returns:
        ``(是否成功, 获得积分, 连续天数, 提示文案)``。
        当天已签到时返回 ``(False, 0, streak, 提示)``。
    """
    today = today_str()
    if user.get("sign_date") == today:
        return False, 0, int(user.get("streak", 0)), "今天已经签到过啦，明天再来～"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if user.get("sign_date") == yesterday:
        streak = int(user.get("streak", 0)) + 1
    else:
        streak = 1

    base = random.randint(min(min_reward, max_reward), max(min_reward, max_reward))
    multiplier = min(max_streak_bonus, 1 + streak_bonus * streak)
    reward = int(base * multiplier)

    user["sign_date"] = today
    user["streak"] = streak
    user["last_sign_ts"] = int(time.time())
    user["total_sign"] = int(user.get("total_sign", 0)) + 1
    user["best_streak"] = max(int(user.get("best_streak", 0)), streak)
    user["balance"] = int(user.get("balance", 0)) + reward

    msg = f"签到成功，获得 {reward} 积分！"
    if streak > 1:
        msg += f" 已连续签到 {streak} 天，奖励加成 ×{multiplier:.2f}。"
    return True, reward, streak, msg


def lottery_draw(
    user: dict, cost: int, cooldown: int, now: float | None = None
) -> tuple[bool, int, str, str]:
    """执行一次抽奖。

    Args:
        user: 用户档案（会被就地更新）。
        cost: 单次消耗积分。
        cooldown: 冷却秒数。
        now: 当前时间戳，默认取系统时间。

    Returns:
        ``(是否成功, 积分变化量, 奖品文案, 提示)``。
        冷却中或积分不足时返回 ``(False, 0, "", 提示)``。
    """
    now = time.time() if now is None else now
    last = float(user.get("last_lottery_ts", 0) or 0)
    remain = cooldown - (now - last)
    if remain > 0:
        return False, 0, "", f"冷却中，还需 {int(remain) + 1} 秒才能再抽。"
    if int(user.get("balance", 0)) < cost:
        return False, 0, "", f"积分不足，抽奖需要 {cost} 积分。"

    weights = [w for w, *_ in LOTTERY_POOL]
    picked = random.choices(LOTTERY_POOL, weights=weights, k=1)[0]
    _, kind, label, value = picked

    user["last_lottery_ts"] = now
    user["lottery_count"] = int(user.get("lottery_count", 0)) + 1

    delta = -cost
    if kind == "amount":
        delta += value
        user["balance"] = max(0, int(user.get("balance", 0)) + delta)
        prize = f"{label}（{value:+d} 积分）" if value else label
    else:
        user["balance"] = max(0, int(user.get("balance", 0)) - cost)
        prize = label
    return True, delta, prize, ""


@dataclass
class GuessGame:
    """猜数字游戏状态。

    Attributes:
        target: 目标数字。
        low: 当前有效下界。
        high: 当前有效上界。
        max_attempts: 最大猜测次数。
        attempts: 已猜测次数。
        started_at: 开始时间戳。
    """

    target: int
    low: int
    high: int
    max_attempts: int
    attempts: int = 0
    started_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, low: int, high: int, max_attempts: int) -> GuessGame:
        """创建一个新的猜数字游戏。

        Args:
            low: 数字下界。
            high: 数字上界。
            max_attempts: 最大猜测次数。

        Returns:
            初始化后的游戏实例。
        """
        lo, hi = min(low, high), max(low, high)
        return cls(
            target=random.randint(lo, hi),
            low=lo,
            high=hi,
            max_attempts=max(1, max_attempts),
        )

    def guess(self, value: int) -> tuple[str, str]:
        """进行一次猜测。

        Args:
            value: 玩家猜测的数字。

        Returns:
            ``(结果, 提示)``，结果为 ``low``/``high``/``win``/``lose``。
        """
        self.attempts += 1
        if value == self.target:
            return "win", f"猜中了！答案就是 {self.target}。"
        if self.attempts >= self.max_attempts:
            return "lose", f"次数用完了，答案是 {self.target}。"
        if value < self.target:
            self.low = max(self.low, value + 1)
            hint = f"太小了，范围是 {self.low} ~ {self.high}。"
        else:
            self.high = min(self.high, value - 1)
            hint = f"太大了，范围是 {self.low} ~ {self.high}。"
        if abs(value - self.target) <= max(1, (self.high - self.low) // 10):
            hint += " " + random.choice(GUESS_HOT_HINTS)
        return ("low" if value < self.target else "high"), hint


@dataclass
class ChainGame:
    """词语接龙游戏状态。

    Attributes:
        last_word: 上一个有效词语。
        last_user: 上一个接龙者的 ID。
        started_at: 开始时间戳。
        round: 已进行的轮次。
        used: 已使用过的词语集合。
    """

    last_word: str
    last_user: str = ""
    started_at: float = field(default_factory=time.time)
    round: int = 0
    used: set[str] = field(default_factory=set)

    def submit(self, word: str, user_id: str) -> tuple[bool, str]:
        """提交一个接龙词语。

        Args:
            word: 玩家提交的词语。
            user_id: 玩家 ID。

        Returns:
            ``(是否有效, 提示)``。无效时 ``提示`` 为失败原因。
        """
        w = word.strip()
        if not w or len(w) < 2:
            return False, "请发一个至少两个字的中文词语。"
        if not all("\u4e00" <= ch <= "\u9fff" for ch in w):
            return False, "只接受纯中文词语哦。"
        if user_id == self.last_user:
            return False, "不能连续两次都由同一个人接龙，等别人接一轮吧～"
        if w in self.used:
            return False, f"「{w}」已经用过啦，换一个。"
        if self.last_word and w[0] != self.last_word[-1]:
            return (
                False,
                f"要接「{self.last_word[-1]}」开头，比如「{self.last_word[-1]}…」。",
            )
        self.last_word = w
        self.last_user = user_id
        self.round += 1
        self.used.add(w)
        return True, f"接龙成功：「{w}」，下一个请接「{w[-1]}」字。"

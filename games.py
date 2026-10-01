"""互动玩法的纯逻辑层。

只放不依赖 AstrBot 运行时的算法与状态机：签到、抽奖、猜数字、接龙、
掷骰、每日任务等。所有函数无外部副作用（不读配置、不写磁盘），
随机性通过传入的 ``rng`` 注入，便于确定性测试与复用。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

# 抽奖奖池：(权重, 类型, 文案, 数值)。amount=积分，item=称号/道具。
LOTTERY_POOL: list[tuple[int, str, str, int]] = [
    (32, "amount", "谢谢参与，下次一定", 0),
    (24, "amount", "小奖", 5),
    (18, "amount", "中奖", 15),
    (12, "amount", "大奖", 40),
    (6, "amount", "超大奖", 120),
    (4, "amount", "暴击大奖", 300),
    (3, "item", "限定称号：欧皇", 0),
    (1, "item", "绝版头像框：天选之人", 0),
]

# 高频台词池，避免回复机械重复
GUESS_HOT_HINTS = ("非常接近了！", "就差一点点！", "感觉很近了！")
SIGN_FLAVORS = (
    "今天也是元气满满的一天～",
    "星光不问赶路人。",
    "坚持这件事，本身就很酷。",
    "签到成功，好运正在派件。",
)
ROB_FLAVORS = ("手速惊人！", "一把梭哈，佩服。", "这波不亏。")

# 每日任务池：(任务码, 描述, 目标次数, 奖励积分)
DAILY_QUESTS: list[tuple[str, str, int, int]] = [
    ("sign", "完成 1 次签到", 1, 10),
    ("lottery", "抽奖 1 次", 1, 8),
    ("guess", "猜数字胜利 1 次", 1, 12),
    ("chain", "接龙成功 3 次", 3, 15),
    ("dice", "掷骰子 3 次", 3, 6),
    ("rob", "打劫成功 1 次", 1, 10),
]
QUEST_TITLE_BY_CODE = {
    "sign": "total_sign",
    "lottery": "lottery_count",
    "guess": "guess_win",
    "chain": "chain_win",
    "dice": "dice_count",
    "rob": "rob_win",
}


def today_str() -> str:
    """返回当天日期字符串（YYYY-MM-DD）。

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
    rng: random.Random | None = None,
) -> tuple[bool, int, int, str]:
    """执行一次签到结算。

    Args:
        user: 用户档案，会被就地更新。
        min_reward: 基础奖励下限。
        max_reward: 基础奖励上限。
        streak_bonus: 连续签到奖励系数。
        max_streak_bonus: 连续奖励倍数上限。
        rng: 随机源，默认使用全局 ``random``。

    Returns:
        ``(是否成功, 获得积分, 连续天数, 提示文案)``。
    """
    rng = rng or random
    today = today_str()
    streak = int(user.get("streak", 0) or 0)
    if user.get("sign_date") == today:
        return False, 0, streak, "今天已经签到过啦，明天再来～"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    streak = streak + 1 if user.get("sign_date") == yesterday else 1

    lo, hi = sorted((int(min_reward), int(max_reward)))
    base = rng.randint(lo, hi) if hi > lo else lo
    # 连续加成从第 2 天起算：首日谈不上「连续」，不应有额外奖励
    bonus_days = max(0, streak - 1)
    multiplier = min(
        max(float(max_streak_bonus), 1.0), 1 + float(streak_bonus) * bonus_days
    )
    reward = max(0, int(base * multiplier))

    user["sign_date"] = today
    user["streak"] = streak
    user["last_sign_ts"] = int(time.time())
    user["total_sign"] = int(user.get("total_sign", 0) or 0) + 1
    user["best_streak"] = max(int(user.get("best_streak", 0) or 0), streak)
    user["balance"] = int(user.get("balance", 0) or 0) + reward
    if user.get("daily_date") != today:
        user["daily_date"] = today
        user["daily_balance"] = 0
    user["daily_balance"] = int(user.get("daily_balance", 0) or 0) + reward

    msg = f"签到成功，获得 {reward} 积分！"
    if streak > 1:
        msg += f" 已连续签到 {streak} 天，奖励加成 ×{multiplier:.2f}。"
    return True, reward, streak, msg


def lottery_draw(
    user: dict,
    cost: int,
    cooldown: int,
    now: float | None = None,
    rng: random.Random | None = None,
) -> tuple[bool, int, str, str]:
    """执行一次抽奖。

    余额结算只在此处发生一次：``余额 = 余额 - 消耗 + 奖金``。

    Args:
        user: 用户档案，会被就地更新。
        cost: 单次消耗积分。
        cooldown: 冷却秒数。
        now: 当前时间戳，默认取系统时间。
        rng: 随机源。

    Returns:
        ``(是否成功, 积分净变化量, 奖品文案, 失败原因)``。
    """
    rng = rng or random
    now = time.time() if now is None else now
    cost = max(0, int(cost))
    remain = float(cooldown) - (now - float(user.get("last_lottery_ts", 0) or 0))
    if remain > 0:
        return False, 0, "", f"冷却中，还需 {int(remain) + 1} 秒才能再抽。"
    balance = int(user.get("balance", 0) or 0)
    if balance < cost:
        return False, 0, "", f"积分不足，抽奖需要 {cost} 积分。"

    weights = [w for w, *_ in LOTTERY_POOL]
    _, kind, label, value = rng.choices(LOTTERY_POOL, weights=weights, k=1)[0]

    user["last_lottery_ts"] = now
    user["lottery_count"] = int(user.get("lottery_count", 0) or 0) + 1

    delta = -cost
    if kind == "amount":
        delta += value
        user["balance"] = max(0, balance + delta)
        prize = f"{label}（{value:+d} 积分）" if value else label
    else:
        user["balance"] = max(0, balance - cost)
        titles = user.setdefault("titles", [])
        if label not in titles:
            titles.append(label)
        user["title"] = label
        prize = f"{label} 🎉"
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
        players: 参与过的用户 ID 集合。
        winner: 猜中的用户 ID。
        started_at: 开始时间戳。
    """

    target: int
    low: int
    high: int
    max_attempts: int
    attempts: int = 0
    players: set[str] = field(default_factory=set)
    winner: str = ""
    started_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls, low: int, high: int, max_attempts: int, rng: random.Random | None = None
    ) -> GuessGame:
        """创建一个新的猜数字游戏。

        Args:
            low: 数字下界。
            high: 数字上界。
            max_attempts: 最大猜测次数。
            rng: 随机源。

        Returns:
            初始化后的游戏实例。
        """
        lo, hi = sorted((int(low), int(high)))
        rng = rng or random
        return cls(
            target=rng.randint(lo, hi),
            low=lo,
            high=hi,
            max_attempts=max(1, int(max_attempts)),
        )

    def guess(self, value: int) -> tuple[str, str]:
        """进行一次猜测。

        Args:
            value: 玩家猜测的数字。

        Returns:
            ``(结果, 提示)``，结果为 ``low``/``high``/``win``/``lose``。
        """
        value = int(value)
        self.attempts += 1
        if value == self.target:
            self.winner = "1"
            return "win", f"猜中了！答案就是 {self.target}。"
        if self.attempts >= self.max_attempts:
            return "lose", f"次数用完了，答案是 {self.target}。"

        distance = abs(value - self.target)
        span = max(1, self.high - self.low)
        if value < self.target:
            self.low = max(self.low, value + 1)
            hint = f"太小了，范围是 {self.low} ~ {self.high}。"
        else:
            self.high = min(self.high, value - 1)
            hint = f"太大了，范围是 {self.low} ~ {self.high}。"
        # 用"与答案的距离"相对当前区间判断接近度，收窄区间后依然有效
        if distance <= max(1, span // 10):
            hint += " " + (rng_choice(GUESS_HOT_HINTS))
        return ("low" if value < self.target else "high"), hint


def rng_choice(seq: tuple[str, ...]) -> str:
    """从文案池随机取一条。

    Args:
        seq: 文案池。

    Returns:
        随机文案。
    """
    return random.choice(seq)


@dataclass
class ChainGame:
    """词语接龙游戏状态。

    Attributes:
        last_word: 上一个有效词语。
        last_user: 上一个接龙者的 ID。
        started_at: 开始时间戳。
        round: 已进行的轮次。
        used: 已使用过的词语集合。
        scores: 用户 ID -> 该局接龙成功次数。
    """

    last_word: str
    last_user: str = ""
    started_at: float = field(default_factory=time.time)
    round: int = 0
    used: set[str] = field(default_factory=set)
    scores: dict[str, int] = field(default_factory=dict)

    def submit(self, word: str, user_id: str) -> tuple[bool, str]:
        """提交一个接龙词语。

        Args:
            word: 玩家提交的词语。
            user_id: 玩家 ID。

        Returns:
            ``(是否有效, 提示)``。
        """
        w = (word or "").strip()
        if len(w) < 2:
            return False, "请发一个至少两个字的中文词语。"
        if len(w) > 12:
            return False, "词语太长了，最多 12 个字。"
        if not all("\u4e00" <= ch <= "\u9fff" for ch in w):
            return False, "只接受纯中文词语哦。"
        if user_id == self.last_user:
            return False, "不能连续两次都由同一个人接龙，等别人接一轮吧～"
        if w in self.used:
            return False, f"「{w}」已经用过啦，换一个。"
        if w[0] != self.last_word[-1]:
            head = self.last_word[-1]
            return False, f"要接「{head}」开头，比如「{head}…」。"
        self.last_word = w
        self.last_user = user_id
        self.round += 1
        self.used.add(w)
        self.scores[user_id] = self.scores.get(user_id, 0) + 1
        return True, f"接龙成功：「{w}」，下一个请接「{w[-1]}」字。"


def roll_dice(
    count: int, faces: int, rng: random.Random | None = None
) -> tuple[list[int], int]:
    """掷骰子。

    Args:
        count: 骰子数量。
        faces: 每颗骰子的面数。
        rng: 随机源。

    Returns:
        ``(每颗点数列表, 点数之和)``。
    """
    rng = rng or random
    count = min(max(1, int(count)), 10)
    faces = min(max(2, int(faces)), 1000)
    rolls = [rng.randint(1, faces) for _ in range(count)]
    return rolls, sum(rolls)


def rob_check(
    attacker: dict,
    victim: dict,
    amount: int,
    rng: random.Random | None = None,
) -> tuple[bool, int, str]:
    """打劫结算。

    成功率随打劫金额占对方余额比例下降：比例越高越难。
    失败时打劫者赔偿 20%（不超过自身余额）。

    Args:
        attacker: 打劫者档案。
        victim: 被劫者档案。
        amount: 打劫积分数。
        rng: 随机源。

    Returns:
        ``(是否成功, 打劫者净变化, 提示)``。
    """
    rng = rng or random
    amount = int(amount)
    if amount <= 0:
        return False, 0, "打劫数量必须大于 0。"
    victim_balance = int(victim.get("balance", 0) or 0)
    if victim_balance < amount:
        return False, 0, f"对方只有 {victim_balance} 积分，不值得出手。"

    ratio = amount / max(1, victim_balance)
    success_rate = min(0.85, max(0.1, 0.9 - ratio * 0.7))
    if rng.random() < success_rate:
        victim["balance"] = victim_balance - amount
        attacker["balance"] = int(attacker.get("balance", 0) or 0) + amount
        attacker["rob_win"] = int(attacker.get("rob_win", 0) or 0) + 1
        return True, amount, f"打劫成功，抢到 {amount} 积分！{rng.choice(ROB_FLAVORS)}"

    penalty = min(int(attacker.get("balance", 0) or 0), max(1, amount // 5))
    attacker["balance"] = int(attacker.get("balance", 0) or 0) - penalty
    attacker["rob_lose"] = int(attacker.get("rob_lose", 0) or 0) + 1
    return False, -penalty, f"打劫失败，被对方反手教训，赔偿 {penalty} 积分。"


def quest_progress(user: dict) -> list[tuple[dict, int, bool]]:
    """计算用户当天各项任务的进度。

    Args:
        user: 用户档案。

    Returns:
        ``[(任务定义, 当前进度, 是否已完成), ...]``。
    """
    today = today_str()
    if user.get("quest_date") != today:
        user["quest_date"] = today
        user["quest_done"] = []

    done = set(user.get("quest_done") or [])
    result: list[tuple[dict, int, bool]] = []
    for code, desc, target, reward in DAILY_QUESTS:
        field_name = QUEST_TITLE_BY_CODE[code]
        current = int(user.get(field_name, 0) or 0)
        # 用「当天完成的次数」近似：以签到日为周期，未签到则从 0 计
        if field_name != "total_sign":
            current = int(user.get(f"daily_{code}", 0) or 0)
        progress = min(current, target)
        result.append(
            (
                {"code": code, "desc": desc, "target": target, "reward": reward},
                progress,
                code in done,
            )
        )
    return result


def claim_quest(user: dict, code: str) -> tuple[bool, int, str]:
    """领取某项每日任务奖励。

    Args:
        user: 用户档案。
        code: 任务码。

    Returns:
        ``(是否成功, 奖励积分, 提示)``。
    """
    today = today_str()
    if user.get("quest_date") != today:
        user["quest_date"] = today
        user["quest_done"] = []
    done = set(user.get("quest_done") or [])
    quest = next((q for q in DAILY_QUESTS if q[0] == code), None)
    if quest is None:
        return False, 0, "没有这个任务哦，发送「每日任务」查看列表。"
    if code in done:
        return False, 0, "这个任务今天已经领过啦。"
    _, desc, target, reward = quest
    field_name = QUEST_TITLE_BY_CODE[code]
    current = int(user.get(field_name if code == "sign" else f"daily_{code}", 0) or 0)
    if current < target:
        return False, 0, f"任务「{desc}」还差 {target - current} 次，继续加油！"
    done.add(code)
    user["quest_done"] = sorted(done)
    user["balance"] = int(user.get("balance", 0) or 0) + reward
    return True, reward, f"任务「{desc}」完成，获得 {reward} 积分！"


def bump_daily(user: dict, code: str) -> None:
    """累加当天某个任务维度的计数。

    Args:
        user: 用户档案。
        code: 任务码。
    """
    key = f"daily_{code}"
    user[key] = int(user.get(key, 0) or 0) + 1


def level_of(user: dict) -> tuple[int, int, int]:
    """根据累计积分计算等级。

    等级 = 每 100 积分一级，最多 100 级。

    Args:
        user: 用户档案。

    Returns:
        ``(等级, 当前等级内积分, 升级所需积分)``。
    """
    total = int(user.get("balance", 0) or 0) + int(user.get("total_sign", 0) or 0) * 5
    level = min(100, total // 100 + 1)
    inner = total % 100
    return level, inner, 100

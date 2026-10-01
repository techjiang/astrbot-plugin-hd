"""互动玩法的纯逻辑层。

只放不依赖 AstrBot 运行时的算法与状态机：签到、抽奖、猜数字、接龙、
掷骰、打劫、每日任务、幸运数字、魔法八球、扎心文案等。

设计约束：
- **无 IO、无全局可变状态**，随机性一律通过 ``rng`` 参数注入，
  便于确定性测试与复用；
- 所有涉及积分的函数都保证「账目守恒」：函数返回的净变化量
  与用户档案里的余额变化必须严格一致（有回归用例守着）；
- 数值边界一律先收敛再运算，外部传入脏数据不会炸。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

# --------------------------------------------------------------------- 常量与文案池

# 抽奖奖池：(权重, 类型, 文案, 数值)。类型 amount=积分、item=称号/头像框。
# 期望值刻意压到「略低于单次消耗」，避免长期刷抽奖变成印钞机。
LOTTERY_POOL: list[tuple[int, str, str, int]] = [
    (32, "amount", "谢谢参与，下次一定", 0),
    (24, "amount", "小奖", 3),
    (18, "amount", "中奖", 8),
    (12, "amount", "大奖", 20),
    (6, "amount", "超大奖", 60),
    (4, "amount", "暴击大奖", 150),
    (3, "item", "限定称号：欧皇", 0),
    (1, "item", "头像框：天选之人", 0),
]

# 高频台词池，避免回复机械重复
GUESS_HOT_HINTS = ("非常接近了！", "就差一点点！", "感觉很近了！")
SIGN_FLAVORS = (
    "今天也是元气满满的一天～",
    "星光不问赶路人。",
    "坚持这件事，本身就很酷。",
    "签到成功，好运正在派件。",
    "又是第一个卷起来的人。",
    "早安，今天也别忘了摸鱼。",
)
ROB_FLAVORS = ("手速惊人！", "一把梭哈，佩服。", "这波不亏。", "干净利落。")
# 失败赔偿相对打劫金额的倍数。同时也是「反刷分」的核心：
# 打劫金额会被 rob_max_amount() 压到 余额/该倍数 以内，
# 于是赔偿永远被自己余额封顶，期望收益恒为 0（零和转移）。
# 该值越大越温和，默认 6（单次最多赔掉 1/6 身家）。
ROB_PENALTY_RATE = 6
ROB_FAIL_FLAVORS = (
    "被对方反手按在地上摩擦。",
    "脚下一滑，赔了夫人又折兵。",
    "对方早就等着你了。",
)
DICE_FLAVORS = ("🎲 骰子出手！", "🎲 听天由命！", "🎲 点数已定！")
CHAIN_FLAVORS = ("漂亮的一手。", "这词接得妙。", "稳！", "有点东西。")
DICE_EMOJI = ("⚀", "⚁", "⚂", "⚃", "⚄", "⚅")

# 幸运数字
LUCKY_MIN, LUCKY_MAX = 1, 100

# 扎心文案（纯娱乐，无积分影响）
ROASTS = (
    "你的积分余额比我的发际线还让人担心。",
    "签到这么勤快，是不是把群当成了打卡机？",
    "抽奖可以，但别把明天的早饭钱抽没了。",
    "接龙接得挺溜，就是不知道语文老师同不同意。",
    "你说得对，但是「互动」是一款…算了，你开心就好。",
    "别人的排行榜靠实力，你的靠签到次数。",
    "你今天的话比积分还多，但都很精彩。",
    "别卷了别卷了，群里的空气都被你卷稀薄了。",
    "你的手速打劫一流，写作业也这么快就好了。",
    "建议给自己发个称号：本群最勤快的摸鱼选手。",
)

# 魔法八球答案池
EIGHT_BALL_ANSWERS = (
    "毫无疑问，可以。",
    "答案是肯定的。",
    "别怀疑，就是现在。",
    "大概率可行。",
    "嗯，我觉得有戏。",
    "再等等，时机未到。",
    "不好说，问问别人。",
    "现在还不是时候。",
    "这个嘛……你懂的。",
    "换个问题吧。",
    "不太行。",
    "别想了，没戏。",
    "你心里其实已经有答案了。",
    "问就是可以。",
    "问就是不行。",
)

# 每日任务池：(任务码, 描述, 目标次数, 奖励积分)
# 进度口径：当天的 daily_<code> 计数（由 bump_daily 累加）。
DAILY_QUESTS: list[tuple[str, str, int, int]] = [
    ("sign", "完成 1 次签到", 1, 8),
    ("lottery", "抽奖 1 次", 1, 6),
    ("guess", "猜数字胜利 1 次", 1, 10),
    ("chain", "接龙成功 3 次", 3, 12),
    ("dice", "掷骰子 3 次", 3, 5),
    ("rob", "打劫成功 1 次", 1, 8),
]
# 任务码 -> 用户档案里的累计字段名
QUEST_ACC_FIELD = {
    "sign": "total_sign",
    "lottery": "lottery_count",
    "guess": "guess_win",
    "chain": "chain_win",
    "dice": "dice_count",
    "rob": "rob_win",
}
# 需要在每日任务面板里出现的顺序
QUEST_ORDER = [q[0] for q in DAILY_QUESTS]


def today_str() -> str:
    """返回当天日期字符串（YYYY-MM-DD）。"""
    return date.today().isoformat()


def _safe_int(value: object, default: int = 0) -> int:
    """把任意值安全转成 int，失败时回退默认值。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize_range(low: object, high: object, min_span: int = 0) -> tuple[int, int]:
    """把上下界收敛成合法区间。

    Args:
        low: 用户配置的（可能是脏数据的）下界。
        high: 用户配置的（可能是脏数据的）上界。
        min_span: 上下界至少相差多少。

    Returns:
        ``(下界, 上界)``，保证 ``下界 <= 上界`` 且跨度不小于 ``min_span``。
    """
    lo = _safe_int(low, 0)
    hi = _safe_int(high, 0)
    if lo > hi:
        lo, hi = hi, lo
    if hi - lo < min_span:
        hi = lo + min_span
    return lo, hi


# --------------------------------------------------------------------- 每日任务进度


def sync_quest_acc(user: dict, today: str | None = None) -> bool:
    """跨天时重置当日任务进度。

    进度以 ``daily_<code>`` 为准（由各玩法在结算成功时调用
    :func:`bump_daily` 累加）。这里只在**确认跨天**时才清零。

    关键细节：``quest_date`` 可能缺失（全新档案、或从旧版本升级上来的
    档案），但 ``daily_*`` 可能已经有值——那是用户当天真实玩出来的。
    因此：

    - ``daily_*`` 已有值时，只补上 ``quest_date``，**不清零**；
    - 确认真的换了日期（``quest_date`` 存在且不等于今天）才清零。

    Returns:
        是否发生了跨天重置。
    """
    today = today or today_str()
    stored = user.get("quest_date")
    if stored == today:
        return False

    has_progress = any(
        _safe_int(user.get(f"daily_{code}", 0), 0) > 0 for code in QUEST_ORDER
    )
    if stored is None and has_progress:
        # 首次补写日期，保留当天已产生的进度
        user["quest_date"] = today
        user.setdefault("quest_done", [])
        return False

    user["quest_date"] = today
    user["quest_done"] = []
    for code in QUEST_ORDER:
        user[f"daily_{code}"] = 0
    return True


def quest_progress(user: dict) -> list[tuple[dict, int, bool]]:
    """计算用户当天各项任务的进度。

    进度直接取当天的 ``daily_<code>`` 计数；若累计值本身已经很大
    （例如 `sign` 的 ``total_sign``），就按「已完成」处理，
    避免老档案在升级插件后第一天看起来「什么都没做」。

    Args:
        user: 用户档案。

    Returns:
        ``[(任务定义, 当前进度, 是否已领取过奖励), ...]``。
    """
    sync_quest_acc(user)
    done = set(user.get("quest_done") or [])
    result: list[tuple[dict, int, bool]] = []
    for code, desc, target, reward in DAILY_QUESTS:
        daily = max(0, _safe_int(user.get(f"daily_{code}", 0), 0))
        total = _safe_int(user.get(QUEST_ACC_FIELD[code], 0), 0)
        current = min(target, daily)
        # 累计数据本身已达标的老档案，视为当天同样达成
        if total >= target:
            current = target
        result.append(
            (
                {"code": code, "desc": desc, "target": target, "reward": reward},
                current,
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
    sync_quest_acc(user)
    done = set(user.get("quest_done") or [])
    quest = next((q for q in DAILY_QUESTS if q[0] == code), None)
    if quest is None:
        return False, 0, "没有这个任务哦，发送「每日任务」查看列表。"
    if code in done:
        return False, 0, "这个任务今天已经领过啦。"
    _, desc, target, reward = quest
    daily = max(0, _safe_int(user.get(f"daily_{code}", 0), 0))
    total = _safe_int(user.get(QUEST_ACC_FIELD[code], 0), 0)
    current = min(target, daily)
    if total >= target:
        current = target
    if current < target:
        return False, 0, f"任务「{desc}」还差 {target - current} 次，继续加油！"
    done.add(code)
    user["quest_done"] = sorted(done)
    user["balance"] = _safe_int(user.get("balance", 0), 0) + reward
    return True, reward, f"任务「{desc}」完成，获得 {reward} 积分！"


def bump_daily(user: dict, code: str) -> None:
    """累加当天某个任务维度的计数（供玩法落地后调用）。"""
    key = f"daily_{code}"
    user[key] = _safe_int(user.get(key, 0), 0) + 1


# --------------------------------------------------------------------- 签到


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
    streak = _safe_int(user.get("streak", 0), 0)
    if user.get("sign_date") == today:
        return False, 0, streak, "今天已经签到过啦，明天再来～"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    streak = streak + 1 if user.get("sign_date") == yesterday else 1

    lo, hi = normalize_range(min_reward, max_reward)
    base = rng.randint(lo, hi) if hi > lo else lo
    # 连续加成从第 2 天起算：首日谈不上「连续」，不应有额外奖励
    bonus_days = max(0, streak - 1)
    cap = max(float(max_streak_bonus), 1.0)
    multiplier = min(cap, 1 + max(0.0, float(streak_bonus)) * bonus_days)
    reward = max(0, int(base * multiplier))

    user["sign_date"] = today
    user["streak"] = streak
    user["last_sign_ts"] = int(time.time())
    user["total_sign"] = _safe_int(user.get("total_sign", 0), 0) + 1
    user["best_streak"] = max(_safe_int(user.get("best_streak", 0), 0), streak)
    user["balance"] = _safe_int(user.get("balance", 0), 0) + reward
    if user.get("daily_date") != today:
        user["daily_date"] = today
        user["daily_balance"] = 0
    user["daily_balance"] = _safe_int(user.get("daily_balance", 0), 0) + reward

    msg = f"签到成功，获得 {reward} 积分！"
    if streak > 1:
        msg += f" 已连续签到 {streak} 天，奖励加成 ×{multiplier:.2f}。"
    return True, reward, streak, msg


# --------------------------------------------------------------------- 抽奖


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
    cost = max(0, _safe_int(cost, 0))
    remain = float(cooldown) - (now - float(user.get("last_lottery_ts", 0) or 0))
    if remain > 0:
        return False, 0, "", f"冷却中，还需 {int(remain) + 1} 秒才能再抽。"
    balance = _safe_int(user.get("balance", 0), 0)
    if balance < cost:
        return False, 0, "", f"积分不足，抽奖需要 {cost} 积分。"

    weights = [w for w, *_ in LOTTERY_POOL]
    _, kind, label, value = rng.choices(LOTTERY_POOL, weights=weights, k=1)[0]

    user["last_lottery_ts"] = now
    user["lottery_count"] = _safe_int(user.get("lottery_count", 0), 0) + 1

    delta = -cost
    if kind == "amount":
        delta += value
        user["balance"] = max(0, balance + delta)
        prize = f"{label}（{value:+d} 积分）" if value else label
    else:
        user["balance"] = max(0, balance - cost)
        titles = user.setdefault("titles", [])
        if not isinstance(titles, list):
            titles = []
            user["titles"] = titles
        if label not in titles:
            titles.append(label)
        user["title"] = label
        prize = f"{label} 🎉"
    return True, delta, prize, ""


# --------------------------------------------------------------------- 猜数字


@dataclass
class GuessGame:
    """猜数字游戏状态。"""

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
        """创建一个新的猜数字游戏。"""
        lo, hi = normalize_range(low, high, min_span=1)
        rng = rng or random
        return cls(
            target=rng.randint(lo, hi),
            low=lo,
            high=hi,
            max_attempts=max(1, _safe_int(max_attempts, 10)),
        )

    def guess(self, value: int, user_id: str = "") -> tuple[str, str]:
        """进行一次猜测。

        Args:
            value: 玩家猜测的数字。
            user_id: 猜测者 ID，用于记录参与者。

        Returns:
            ``(结果, 提示)``，结果为 ``low``/``high``/``win``/``lose``。
        """
        value = _safe_int(value, 0)
        if user_id:
            self.players.add(str(user_id))
        self.attempts += 1
        if value == self.target:
            self.winner = str(user_id) or "1"
            return "win", f"猜中了！答案就是 {self.target}。"

        # 接近度用「改动前的区间跨度」做基准，收窄后依然有效
        distance = abs(value - self.target)
        span = max(1, self.high - self.low)

        if self.attempts >= self.max_attempts:
            return "lose", f"次数用完了，答案是 {self.target}。"

        if value < self.target:
            self.low = max(self.low, value + 1)
            hint = f"太小了，范围是 {self.low} ~ {self.high}。"
        else:
            self.high = min(self.high, value - 1)
            hint = f"太大了，范围是 {self.low} ~ {self.high}。"
        if distance <= max(1, span // 10):
            hint += " " + random.choice(GUESS_HOT_HINTS)
        return ("low" if value < self.target else "high"), hint


def rng_choice(seq: tuple[str, ...]) -> str:
    """从文案池随机取一条。"""
    return random.choice(seq)


# --------------------------------------------------------------------- 词语接龙


@dataclass
class ChainGame:
    """词语接龙游戏状态。"""

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
        user_id = str(user_id)
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


# --------------------------------------------------------------------- 掷骰


def roll_dice(
    count: int, faces: int, rng: random.Random | None = None
) -> tuple[list[int], int]:
    """掷骰子。

    Args:
        count: 骰子数量，收敛到 1~10。
        faces: 每颗骰子的面数，收敛到 2~1000。
        rng: 随机源。

    Returns:
        ``(每颗点数列表, 点数之和)``。
    """
    rng = rng or random
    count = min(max(1, _safe_int(count, 1)), 10)
    faces = min(max(2, _safe_int(faces, 6)), 1000)
    rolls = [rng.randint(1, faces) for _ in range(count)]
    return rolls, sum(rolls)


def dice_faces_text(rolls: list[int], faces: int) -> str:
    """把骰子点数渲染成直观文本。

    六面骰用 ⚀~⚅ 字符，其它面数直接显示数字；大点数时可能无法用
    字符表示，此函数会回退到数字。

    Args:
        rolls: 每颗骰子的点数。
        faces: 面数。

    Returns:
        用于展示的字符串，如 ``⚂ ⚄`` 或 ``14 + 5 + 7``。
    """
    if faces == 6 and len(rolls) <= 10 and all(1 <= r <= 6 for r in rolls):
        return " ".join(DICE_EMOJI[r - 1] for r in rolls)
    return " + ".join(str(r) for r in rolls)


# --------------------------------------------------------------------- 打劫


def rob_success_rate(amount: int, victim_balance: int) -> float:
    """计算打劫成功率。

    相对打劫比例（``amount / victim_balance``）越大越容易失手。

    Args:
        amount: 打劫积分数。
        victim_balance: 对方余额。

    Returns:
        0.25 ~ 0.85 之间的成功率；参数非法时返回 0。
    """
    amount = max(0, _safe_int(amount, 0))
    victim_balance = max(0, _safe_int(victim_balance, 0))
    if amount <= 0 or victim_balance <= 0:
        return 0.0
    ratio = min(1.0, amount / max(1, victim_balance))
    return min(0.85, max(0.85 - ratio * 0.60, 0.25))


def rob_max_amount(attacker_balance: int, victim_balance: int) -> int:
    """计算允许打劫的最大积分。

    采用**期望中性设计**：打劫的期望收益恒等于 0。

    设成功率为 ``p``、赔偿倍数为 ``R = ROB_PENALTY_RATE``，
    期望 ``E = p·a − (1−p)·R·a``。只要把打劫金额 ``a`` 压到
    ``attacker_balance / R`` 以内，失败赔偿就被「自己的余额」封顶，
    期望自动归零。因此打劫只是**零和的资产转移**，既不会凭空造币，
    也不会出现「余额为 0 就零成本无限试错」的漏洞。

    Args:
        attacker_balance: 打劫者余额。
        victim_balance: 被劫者余额。

    Returns:
        允许的最大打劫金额，单位为积分。
    """
    attacker_balance = max(0, _safe_int(attacker_balance, 0))
    victim_balance = max(0, _safe_int(victim_balance, 0))
    return min(victim_balance, attacker_balance // ROB_PENALTY_RATE)


def rob_penalty(amount: int, attacker_balance: int) -> int:
    """计算打劫失败时的赔偿额。

    赔偿 = ``打劫金额 × ROB_PENALTY_RATE``，以「净身出户」为上限。

    Args:
        amount: 打劫积分数。
        attacker_balance: 打劫者当前余额。

    Returns:
        实际赔偿积分（不会超过打劫者余额）。
    """
    amount = max(0, _safe_int(amount, 0))
    attacker_balance = max(0, _safe_int(attacker_balance, 0))
    return min(int(amount * ROB_PENALTY_RATE), attacker_balance)


def rob_expected_value(
    amount: int, victim_balance: int, attacker_balance: int
) -> float:
    """打劫的期望净收益（用于测试与数值调参，不参与运行时逻辑）。

    只要 ``amount <= rob_max_amount(attacker_balance, victim_balance)``，
    返回值必然为 0。
    """
    p = rob_success_rate(amount, victim_balance)
    if p <= 0:
        return 0.0
    return p * amount - (1 - p) * rob_penalty(amount, attacker_balance)


def rob_check(
    attacker: dict,
    victim: dict,
    amount: int,
    rng: random.Random | None = None,
) -> tuple[bool, int, str]:
    """打劫结算。

    安全性来自两条约束：

    1. ``amount`` 会被收敛到 ``rob_max_amount()``，失败赔偿天然被自己
       的余额封顶，期望收益恒为 0 —— 打劫只能转移资产，不能造币；
    2. 赔偿倍数是余额的 1/6，单次失手的最大损失是「六分之一的余额」。

    Args:
        attacker: 打劫者档案，会被就地更新。
        victim: 被劫者档案，会被就地更新。
        amount: 打劫积分数。
        rng: 随机源。

    Returns:
        ``(是否成功, 打劫者净变化, 提示)``。
    """
    rng = rng or random
    amount = _safe_int(amount, 0)
    if amount <= 0:
        return False, 0, "打劫数量必须大于 0。"

    victim_balance = max(0, _safe_int(victim.get("balance", 0), 0))
    if victim_balance <= 0:
        return False, 0, "对方身无分文，不值得出手。"
    if victim_balance < amount:
        return False, 0, f"对方只有 {victim_balance} 积分，不值得出手。"
    attacker_balance = max(0, _safe_int(attacker.get("balance", 0), 0))

    cap = rob_max_amount(attacker_balance, victim_balance)
    if cap <= 0:
        return (
            False,
            0,
            f"你穷得连赔偿都赔不起，先攒够 {ROB_PENALTY_RATE} 积分再来打劫吧。",
        )
    if amount > cap:
        amount = cap

    if rng.random() < rob_success_rate(amount, victim_balance):
        victim["balance"] = victim_balance - amount
        attacker["balance"] = attacker_balance + amount
        attacker["rob_win"] = _safe_int(attacker.get("rob_win", 0), 0) + 1
        return True, amount, f"打劫成功，抢到 {amount} 积分！{rng.choice(ROB_FLAVORS)}"

    penalty = rob_penalty(amount, attacker_balance)
    attacker["balance"] = attacker_balance - penalty
    attacker["rob_lose"] = _safe_int(attacker.get("rob_lose", 0), 0) + 1
    return (
        False,
        -penalty,
        f"打劫失败，赔偿 {penalty} 积分。{rng.choice(ROB_FAIL_FLAVORS)}",
    )


# --------------------------------------------------------------------- 等级 / 幸运数字 / 八球 / 扎心


def level_of(user: dict) -> tuple[int, int, int, int]:
    """根据累计经验计算等级。

    经验 = 余额 + 签到天数 × 5，每 100 点一级，最高 100 级。

    Args:
        user: 用户档案。

    Returns:
        ``(等级, 当前等级内经验, 升级所需经验, 经验总量)``。
    """
    total = max(
        0,
        _safe_int(user.get("balance", 0), 0)
        + _safe_int(user.get("total_sign", 0), 0) * 5,
    )
    level = min(100, total // 100 + 1)
    inner = total % 100
    return level, inner, 100, total


def lucky_number(day: str, uid: str) -> int:
    """按「日期 + 用户」确定性地算出当天幸运数字。

    同一个人在同一天拿到的结果稳定不变（便于用户对照自己是否「中」了），
    换天或换人都会变。

    Args:
        day: 日期字符串（YYYY-MM-DD）。
        uid: 用户 ID。

    Returns:
        1~100 之间的幸运数字。
    """
    seed = f"{day}:{uid}"
    # 用 FNV-1a 做稳定散列，避免依赖 PYTHONHASHSEED
    h = 0x811C9DC5
    for ch in seed:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % (LUCKY_MAX - LUCKY_MIN + 1) + LUCKY_MIN


def lucky_hit(day: str, uid: str, guess: int) -> bool:
    """判断用户今天报的数字是否命中幸运数字。"""
    return _safe_int(guess, -1) == lucky_number(day, uid)


def eight_ball(question: str, rng: random.Random | None = None) -> tuple[bool, str]:
    """魔法八球：给一个是/否问题的答案。

    Args:
        question: 用户的问题。
        rng: 随机源。

    Returns:
        ``(是否有效, 答案或提示)``。
    """
    rng = rng or random
    q = (question or "").strip()
    if not q:
        return False, "用法：/八球 <你的问题>，例如：/八球 今天要加班吗"
    if len(q) > 100:
        q = q[:100] + "…"
    return True, f"🔮 关于「{q}」：{rng.choice(EIGHT_BALL_ANSWERS)}"


def roast(target: str, rng: random.Random | None = None) -> str:
    """生成一句针对某人的扎心文案（纯娱乐）。"""
    rng = rng or random
    name = (target or "").strip() or "你"
    return f"@{name} {rng.choice(ROASTS)}"


def percentile_of(values: list[int], value: int) -> float:
    """计算某数值在一组数值中的百分位（0~100）。

    Args:
        values: 参照数值列表。
        value: 待比较的数值。

    Returns:
        高于多少百分比的人（0~100）。
    """
    if not values:
        return 0.0
    lower = sum(1 for v in values if v < value)
    return lower / len(values) * 100.0


def format_duration(seconds: float) -> str:
    """把秒数格式化成「x 天 y 小时」这类可读文本。"""
    seconds = max(0, _safe_int(seconds, 0))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分钟"
    return f"{seconds // 86400} 天 {seconds % 86400 // 3600} 小时"


def bar(current: int, total: int, width: int = 10) -> str:
    """生成文字进度条。

    Args:
        current: 当前值。
        total: 总值，<=0 时返回空条。
        width: 条宽（字符数）。

    Returns:
        形如 ``▰▰▰▱▱▱▱▱▱▱`` 的进度条。
    """
    width = max(1, _safe_int(width, 10))
    if total <= 0:
        return "▱" * width
    filled = min(width, max(0, current) * width // total)
    return "▰" * filled + "▱" * (width - filled)

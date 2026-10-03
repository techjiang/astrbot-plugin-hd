"""互动玩法的第四批扩展：群体竞技与协作玩法的纯逻辑层。

与其余 ``games_*.py`` 保持同一套设计约束：

- **无 IO、无全局可变状态**，随机性一律通过 ``rng`` 注入，便于测试复现；
- 涉及积分的函数保证**账目守恒**（返回值与余额变化严格一致）；
- 数值边界先收敛再运算，脏配置不会把插件炸掉；
- 展示文案集中成常量池。

覆盖：拍卖行、团队拔河、接力答题（知识竞速）、幸运转盘大富翁、
公会/战队（群内小队）。

经济模型上，本模块**刻意不产出积分**：
拍卖与拔河都是零和转移（有人赚必有对应的人亏），
题库答题的奖励来自调用方传入的固定额度，本模块不负责发放。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .games import _safe_int

# --------------------------------------------------------------------- 通用


def _clamp_int(value: object, lo: int, hi: int, default: int = 0) -> int:
    """把任意值收敛到 ``[lo, hi]`` 区间。

    Args:
        value: 原始值。
        lo: 下界。
        hi: 上界。
        default: 转换失败时的回退值（同样会被收敛）。

    Returns:
        收敛后的整数。
    """
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        result = default
    return min(max(result, lo), hi)


def _clean(text: object) -> str:
    """归一化名称类文本：去空白、限长。

    Args:
        text: 原始文本。

    Returns:
        归一化后的文本。
    """
    return " ".join(str(text or "").split())[:24]


# --------------------------------------------------------------------- 拍卖行

# 拍卖默认时长区间（秒）
AUCTION_MIN_DURATION = 60
AUCTION_MAX_DURATION = 3600
# 一次拍卖最多接受的出价次数，防止刷屏式抬价把内存撑大
AUCTION_MAX_BIDS = 500


@dataclass
class AuctionItem:
    """拍卖标的。

    Attributes:
        item_id: 道具 ID（对应商店商品）。
        name: 显示名。
        price: 商店参考价，用于展示「捡漏/溢价」。
        kind: 道具类型（``title`` / ``frame`` / ``consumable``），
            决定成交后该放回哪一类收藏。
    """

    item_id: str
    name: str
    price: int = 0
    kind: str = "consumable"


@dataclass
class Auction:
    """一场拍卖的状态。

    出价规则是「英式拍卖」：必须严格高于当前最高价，最小加价幅度为
    ``min_increment``。拍卖结束时最高出价者得标，扣款由调用方通过
    ``settle()`` 的返回值完成，本类不直接改用户余额。

    Attributes:
        item: 标的。
        owner: 发起人。
        start_price: 起拍价。
        min_increment: 最小加价幅度。
        bids: ``uid -> 出价``，保留每人最后一次出价。
        bid_order: 出价时间顺序 ``[(uid, amount, ts)]``。
        started_at: 开始时间戳。
        duration: 时长（秒）。
        settled: 是否已结算。
        winner: 中标者。
        final_price: 成交价。
    """

    item: AuctionItem
    owner: str = ""
    start_price: int = 0
    min_increment: int = 1
    bids: dict[str, int] = field(default_factory=dict)
    bid_order: list[tuple[str, int, float]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    duration: int = 300
    settled: bool = False
    winner: str = ""
    final_price: int = 0

    @classmethod
    def new(
        cls,
        item: AuctionItem,
        start_price: int = 0,
        owner: str = "",
        duration: int = 300,
        min_increment: int = 1,
    ) -> Auction:
        """创建一场拍卖。

        Args:
            item: 标的。
            start_price: 起拍价（>= 0）。
            owner: 发起人。
            duration: 时长（秒），收敛到 60 ~ 3600。
            min_increment: 最小加价幅度（>= 1）。

        Returns:
            初始化后的拍卖。
        """
        return cls(
            item=item,
            owner=str(owner),
            start_price=_clamp_int(start_price, 0, 10**9, 0),
            min_increment=max(1, _clamp_int(min_increment, 1, 10**6, 1)),
            duration=_clamp_int(
                duration, AUCTION_MIN_DURATION, AUCTION_MAX_DURATION, 300
            ),
        )

    @property
    def top_bid(self) -> int:
        """当前最高出价。

        Returns:
            最高出价；无人出价时等于起拍价。
        """
        if not self.bids:
            return self.start_price
        return max(self.bids.values())

    @property
    def top_bidder(self) -> str:
        """当前最高出价者。

        Returns:
            用户 ID；无人出价时为空串。
        """
        if not self.bids:
            return ""
        # 同价时以更早出价者为准（先到先得，避免最后一秒同价抢占）
        best_uid, best_amt = "", -1
        for uid, amount, _ts in self.bid_order:
            if amount >= best_amt:
                best_amt, best_uid = amount, uid
        return best_uid

    def expired(self, now: float | None = None) -> bool:
        """是否已到结束时间。

        Args:
            now: 当前时间戳，默认取系统时间。

        Returns:
            是否过期。
        """
        now = time.time() if now is None else now
        return now - self.started_at >= self.duration

    def min_next_bid(self) -> int:
        """下一次出价的最低金额。

        Returns:
            最低可出价金额。
        """
        if not self.bids:
            return self.start_price
        return self.top_bid + self.min_increment

    def place_bid(self, uid: str, amount: int) -> tuple[bool, str]:
        """出价。

        Args:
            uid: 出价人。
            amount: 出价金额。

        Returns:
            ``(是否成功, 提示)``。
        """
        if self.settled:
            return False, "这场拍卖已经结束啦。"
        if len(self.bid_order) >= AUCTION_MAX_BIDS:
            return False, "出价太频繁，等这场结束吧。"
        amount = _clamp_int(amount, 0, 10**9, 0)
        if amount <= 0:
            return False, "出价必须大于 0。"
        if self.owner and uid == self.owner:
            return False, "不能给自己的拍卖出价哦。"
        floor = self.min_next_bid()
        if self.bids and amount <= self.top_bid:
            return False, f"出价要高于当前最高价 {self.top_bid}，最低出 {floor}。"
        if not self.bids and amount < self.start_price:
            return False, f"出价不能低于起拍价 {self.start_price}。"

        self.bids[uid] = amount
        self.bid_order.append((uid, amount, time.time()))
        return True, f"出价成功：{amount} 互动币（当前最高）。"

    def settle(self, now: float | None = None) -> dict[str, object]:
        """结算拍卖。

        调用方负责按返回值扣款与交付道具：本方法只做判定，
        不触碰任何用户档案，保证账目逻辑单一出口。

        Args:
            now: 当前时间戳，默认取系统时间。

        Returns:
            结算结果字典，字段：

            - ``ok``: 是否成功结算（重复结算返回 False）；
            - ``sold``: 是否有人得标；
            - ``winner``: 得标者；
            - ``price``: 成交价；
            - ``item``: 标的；
            - ``note``: 展示文案。
        """
        empty = {
            "ok": False,
            "sold": False,
            "winner": "",
            "price": 0,
            "item": self.item,
            "note": "这场拍卖已经结算过了。",
        }
        if self.settled:
            return empty
        self.settled = True

        winner = self.top_bidder
        if not winner:
            return {
                "ok": True,
                "sold": False,
                "winner": "",
                "price": 0,
                "item": self.item,
                "note": f"🔨 「{self.item.name}」流拍，无人出价。",
            }
        self.winner = winner
        self.final_price = self.bids[winner]
        return {
            "ok": True,
            "sold": True,
            "winner": winner,
            "price": self.final_price,
            "item": self.item,
            "note": (
                f"🔨 拍卖成交！「{self.item.name}」由 {winner} 以 "
                f"{self.final_price} 互动币拍得。"
            ),
        }


# --------------------------------------------------------------------- 团队拔河


@dataclass
class TugTeam:
    """拔河队的一条记录。

    Attributes:
        name: 队名。
        members: 队员 ID 集合。
        power: 累计投入的力量。
    """

    name: str
    members: set[str] = field(default_factory=set)
    power: int = 0


@dataclass
class TugOfWar:
    """团队拔河：两个阵营各投力量，力量高者胜。

    拔河刻意设计成**人员归属决定胜负**：投入的力量只用于比大小，
    不直接从投入者身上扣分（扣分由调用方按 ``stake`` 决定）。
    这样队伍可以自由拉人，而不会把「投得多」变成「亏得多」。

    Attributes:
        teams: 队伍名 -> 队伍。
        prizes: 胜方每人奖金（由调用方在结算时发放）。
        started_at: 开始时间戳。
        duration: 时长（秒）。
        settled: 是否已结算。
        winner: 胜方队名。
        tie: 是否平局。
    """

    teams: dict[str, TugTeam] = field(default_factory=dict)
    prizes: int = 0
    started_at: float = field(default_factory=time.time)
    duration: int = 300
    settled: bool = False
    winner: str = ""
    tie: bool = False

    @classmethod
    def new(cls, names: list[str], duration: int = 300, prizes: int = 0) -> TugOfWar:
        """创建一场拔河。

        Args:
            names: 两队队名（至少 2 个，多余会被截断）。
            duration: 时长（秒），收敛到 30 ~ 3600。
            prizes: 胜方每人奖金。

        Returns:
            初始化后的拔河。
        """
        cleaned = [_clean(n) for n in names if _clean(n)][:2]
        teams = {n: TugTeam(name=n) for n in cleaned}
        return cls(
            teams=teams,
            prizes=max(0, _clamp_int(prizes, 0, 10**6, 0)),
            duration=_clamp_int(duration, 30, 3600, 300),
        )

    def join(self, team: str, uid: str, power: int) -> tuple[bool, str]:
        """加入某队并投入力量。

        同一人重复投入会累加力量，但**不可换队**（换队会让战术失去意义）。

        Args:
            team: 队名。
            uid: 用户 ID。
            power: 投入的力量。

        Returns:
            ``(是否成功, 提示)``。
        """
        if self.settled:
            return False, "这场拔河已经结束啦。"
        if team not in self.teams:
            return False, f"没有「{team}」这个队，可选：{' / '.join(self.teams)}。"
        power = _clamp_int(power, 0, 10**6, 0)
        if power <= 0:
            return False, "投入的力量必须大于 0。"

        for other in self.teams.values():
            if other.name != team and uid in other.members:
                return False, f"你已经加入「{other.name}」队了，不能中途换队哦。"

        target = self.teams[team]
        target.members.add(uid)
        target.power += power
        return True, f"你加入了「{team}」队，投入 {power} 力量（当前 {target.power}）。"

    def expired(self, now: float | None = None) -> bool:
        """是否已到结束时间。

        Args:
            now: 当前时间戳，默认取系统时间。

        Returns:
            是否过期。
        """
        now = time.time() if now is None else now
        return now - self.started_at >= self.duration

    def settle(self) -> dict[str, object]:
        """结算拔河。

        Returns:
            结算结果字典，字段 ``ok`` / ``winner`` / ``tie`` /
            ``winners``（胜方成员列表）/ ``note``。
        """
        if self.settled:
            return {
                "ok": False,
                "winner": "",
                "tie": False,
                "winners": [],
                "note": "这场拔河已经结算过了。",
            }
        self.settled = True

        ranked = sorted(self.teams.values(), key=lambda t: t.power, reverse=True)
        if not ranked or (len(ranked) >= 2 and ranked[0].power == ranked[1].power):
            self.tie = True
            return {
                "ok": True,
                "winner": "",
                "tie": True,
                "winners": [],
                "note": "⚖️ 双方力量相同，平局！奖金不发。",
            }
        best = ranked[0]
        self.winner = best.name
        return {
            "ok": True,
            "winner": best.name,
            "tie": False,
            "winners": sorted(best.members),
            "note": (
                f"🏅 「{best.name}」队以 {best.power} 力量获胜！"
                f"队员 {len(best.members)} 人各得 {self.prizes} 互动币。"
            ),
        }

    def render(self) -> str:
        """渲染拔河战况。

        Returns:
            多行展示文本。
        """
        total = sum(t.power for t in self.teams.values()) or 1
        lines = ["🪢 团队拔河进行中"]
        for team in self.teams.values():
            ratio = int(team.power / total * 20)
            lines.append(
                f"  {team.name} — {team.power} 力量"
                f"｜{len(team.members)} 人｜{'█' * ratio}{'░' * (20 - ratio)}"
            )
        left = max(0, int(self.duration - (time.time() - self.started_at)))
        lines.append(f"  剩余 {left // 60} 分 {left % 60} 秒")
        return "\n".join(lines)


# --------------------------------------------------------------------- 知识竞速

# 题库：(题目, 可接受答案列表)。答案一律小写、去空白后匹配。
QUIZ_BANK: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("中国的首都是哪座城市？", ("北京", "beijing")),
    ("一年有多少个月？", ("12", "十二")),
    ("太阳系中最大的行星是？", ("木星", "jupiter")),
    ("水的化学式是什么？", ("h2o", "H2O".lower())),
    ("《西游记》的作者是谁？", ("吴承恩",)),
    ("一年中白天最长的节气是？", ("夏至",)),
    ("光在真空中的速度约每秒多少万公里？", ("30", "三十")),
    ("地球的天然卫星叫什么？", ("月球", "月亮")),
    ("汉字「休」由哪两个字组成？（用「+」连接）", ("人+木", "亻+木")),
    ("圆周率约等于多少？（保留两位小数）", ("3.14",)),
    ("世界上最高的山峰是？", ("珠穆朗玛峰", "珠峰")),
    ("中国的国球是什么？", ("乒乓球",)),
    ("彩虹有几种颜色？", ("7", "七")),
    ("一天有多少小时？", ("24", "二十四")),
    ("《三国演义》中「三顾茅庐」请的是谁？", ("诸葛亮",)),
    ("人体最大的器官是？", ("皮肤",)),
    ("「不惑之年」指的是多少岁？", ("40", "四十")),
    ("中国的四大发明之一，用于航海的是？", ("指南针",)),
)


def normalize_answer(text: object) -> str:
    """归一化答案文本，便于比对。

    Args:
        text: 原始答案。

    Returns:
        去掉空白与常见标点、统一小写的文本。
    """
    raw = "".join(str(text or "").split()).lower()
    return raw.strip("，,。.、；;：:！!？?「」『』【】()（）")


def quiz_match(question_answer: tuple[str, ...], text: object) -> bool:
    """判断用户输入是否命中给定答案集合。

    Args:
        question_answer: 该题的可接受答案列表。
        text: 用户输入。

    Returns:
        是否命中。
    """
    got = normalize_answer(text)
    if not got:
        return False
    return any(got == normalize_answer(a) for a in question_answer)


@dataclass
class QuizRush:
    """知识竞速：出题后第一个答对的人得分。

    与「抢答（谁最先）」的区别是：抢答比的是打字速度、目标词任意；
    知识竞速比的是**知不知道答案**，且答错会有短暂禁答，避免穷举。

    Attributes:
        question: 当前题目。
        answers: 可接受答案。
        owner: 出题人。
        started_at: 出题时间戳。
        timeout: 单题时限（秒）。
        attempts: ``uid -> 已答错次数``。
        locked_until: ``uid -> 解禁时间戳``（答错后短暂禁答）。
        solved_by: 答对者。
    """

    question: str
    answers: tuple[str, ...]
    owner: str = ""
    started_at: float = field(default_factory=time.time)
    timeout: int = 60
    attempts: dict[str, int] = field(default_factory=dict)
    locked_until: dict[str, float] = field(default_factory=dict)
    solved_by: str = ""

    # 答错后的禁答时长（秒），防止靠穷举刷中
    WRONG_LOCK = 3.0
    # 单人最多答错次数，超过后本题对该用户关闭
    MAX_WRONG = 5

    @classmethod
    def new(
        cls,
        rng: random.Random | None = None,
        timeout: int = 60,
        owner: str = "",
        exclude: tuple[str, ...] = (),
    ) -> QuizRush:
        """从题库随机出一题。

        Args:
            rng: 随机数发生器，便于测试复现。
            timeout: 单题时限（秒），收敛到 10 ~ 600。
            owner: 出题人。
            exclude: 需要排除的题目（避免连续重复）。

        Returns:
            初始化后的知识竞速。
        """
        rng = rng or random.Random()
        pool = [q for q in QUIZ_BANK if q[0] not in exclude] or list(QUIZ_BANK)
        question, answers = rng.choice(pool)
        return cls(
            question=question,
            answers=tuple(answers),
            owner=str(owner),
            timeout=_clamp_int(timeout, 10, 600, 60),
        )

    def expired(self, now: float | None = None) -> bool:
        """本题是否已超时。

        Args:
            now: 当前时间戳，默认取系统时间。

        Returns:
            是否超时。
        """
        now = time.time() if now is None else now
        return now - self.started_at >= self.timeout

    def locked_for(self, uid: str, now: float | None = None) -> float:
        """查询某用户还需等待多久才能再答。

        Args:
            uid: 用户 ID。
            now: 当前时间戳，默认取系统时间。

        Returns:
            剩余禁答秒数；未禁答时为 0。
        """
        now = time.time() if now is None else now
        remain = self.locked_until.get(uid, 0.0) - now
        return max(0.0, remain)

    def submit(
        self, uid: str, text: object, now: float | None = None
    ) -> tuple[str, str]:
        """提交一个答案。

        Args:
            uid: 答题人。
            text: 用户输入。
            now: 当前时间戳，默认取系统时间。

        Returns:
            ``(结果, 提示)``。结果为：

            - ``correct``: 答对；
            - ``wrong``: 答错，提示里带剩余机会；
            - ``locked``: 仍在禁答期；
            - ``closed``: 本题已结束 / 该用户机会用完。
        """
        now = time.time() if now is None else now
        if self.solved_by:
            return "closed", "本题已经被抢答啦。"
        if self.expired(now):
            return "closed", f"⏰ 超时！答案是「{self.answers[0]}」。"

        remain = self.locked_for(uid, now)
        if remain > 0:
            return "locked", f"答错后要等 {int(remain) + 1} 秒才能再答哦。"

        if quiz_match(self.answers, text):
            self.solved_by = uid
            return "correct", f"✅ 答对啦！答案就是「{self.answers[0]}」。"

        self.attempts[uid] = self.attempts.get(uid, 0) + 1
        left = self.MAX_WRONG - self.attempts[uid]
        if left <= 0:
            return "closed", "你答错太多次啦，这题交给别人吧。"
        self.locked_until[uid] = now + self.WRONG_LOCK
        return "wrong", f"❌ 不对哦，本题还有 {left} 次机会。"


# --------------------------------------------------------------------- 大富翁

# 棋盘格子的类型与效果。
#
# ⚠️ 经济约束：**一圈（走完所有格子）的净变化必须 <= 0**。
# 这条由 ``tests/test_arena.py::test_monopoly_lap_is_not_a_printer`` 守着。
# 一开始我按「奖励导向」配了一版（一圈 +50），写守恒脚本当场报警 ——
# 每次掷骰都期望正收益，等于又一个印钞机。现在配成一圈 -11（轻微通缩），
# 单格仍有 +30 的高光时刻，但长期必亏，只能当娱乐不能当刷分路径。
# 改文案里的数字时务必同步检查这一圈总和，测试会拦。
MONOPOLY_START_BONUS = 0
MONOPOLY_TILES: tuple[tuple[str, str], ...] = (
    ("start", "起点 · 整装待发"),
    ("gain", "路边捡到零钱 +5"),
    ("toll", "交过路费 -10"),
    ("boom", "投资分红 +10"),
    ("tax", "缴税 -16"),
    ("rest", "休息区 · 喘口气"),
    ("gain", "帮邻居搬东西 +4"),
    ("toll", "请客吃饭 -13"),
    ("boom", "中了个小奖 +8"),
    ("trap", "踩到香蕉皮 -11"),
    ("rest", "补给站 · 原地待命"),
    ("jackpot", "幸运格 +12"),
)


def monopoly_tile(index: int) -> tuple[str, str]:
    """按棋盘索引取格子。

    Args:
        index: 格子索引（会自动取模回环）。

    Returns:
        ``(格子类型, 文案)``。
    """
    return MONOPOLY_TILES[index % len(MONOPOLY_TILES)]


def monopoly_delta(kind: str) -> int:
    """解析格子类型对应的积分变化量。

    文案里的数字由这里统一解析，避免两处各写一份而对不上。

    Args:
        kind: 格子类型。

    Returns:
        积分变化量（正数为收益、负数为支出）。
    """
    if kind == "start":
        return MONOPOLY_START_BONUS
    for t, text in MONOPOLY_TILES:
        if t == kind and ("+" in text or "-" in text):
            import re

            m = re.search(r"[+-]\d+", text)
            if m:
                return int(m.group())
    return 0


@dataclass
class DiceRun:
    """掷骰大富翁：掷骰前进，落在哪个格子就触发对应效果。

    轨迹只前进不后退（取模回环），一轮 = 走完一圈。

    Attributes:
        position: 当前格子索引。
        laps: 已完成的圈数。
        steps: 总步数。
        history: ``(格子文案, 变化量)`` 轨迹。
    """

    position: int = 0
    laps: int = 0
    steps: int = 0
    history: list[tuple[str, int]] = field(default_factory=list)

    MAX_STEPS = 10000

    def roll(
        self, rng: random.Random | None = None, times: int = 1
    ) -> list[tuple[str, int]]:
        """掷骰前进并结算落点。

        Args:
            rng: 随机数发生器，便于测试复现。
            times: 掷骰次数，收敛到 1 ~ 20。

        Returns:
            本次产生的 ``(格子文案, 变化量)`` 轨迹列表。
        """
        rng = rng or random.Random()
        times = _clamp_int(times, 1, 20, 1)
        produced: list[tuple[str, int]] = []
        for _ in range(times):
            if self.steps >= self.MAX_STEPS:
                break
            step = rng.randint(1, 6)
            self.position += step
            self.steps += 1
            while self.position >= len(MONOPOLY_TILES):
                self.position -= len(MONOPOLY_TILES)
                self.laps += 1
            kind, text = monopoly_tile(self.position)
            delta = monopoly_delta(kind)
            self.history.append((text, delta))
            produced.append((text, delta))
        # 轨迹只留最近 20 条，避免长期运行内存膨胀
        if len(self.history) > 20:
            self.history = self.history[-20:]
        return produced

    def total_delta(self, produced: list[tuple[str, int]]) -> int:
        """统计一批轨迹的积分变化总量。

        Args:
            produced: ``roll()`` 的返回值。

        Returns:
            变化总量。
        """
        return sum(d for _t, d in produced)

    def render(self, produced: list[tuple[str, int]]) -> str:
        """渲染本次掷骰过程。

        Args:
            produced: ``roll()`` 的返回值。

        Returns:
            多行展示文本。
        """
        lines = [f"🎲 前进 {self.steps} 步 · 第 {self.laps + 1} 圈"]
        for text, delta in produced:
            sign = f"{delta:+d}" if delta else "±0"
            lines.append(f"  · {text}（{sign}）")
        total = self.total_delta(produced)
        lines.append(f"  合计 {total:+d} 互动币，当前位置第 {self.position + 1} 格")
        return "\n".join(lines)


# --------------------------------------------------------------------- 战队

# 战队等级门槛：按「战队总贡献」分级
SQUAD_TIERS = (
    (0, "🌱 萌芽"),
    (500, "🌿 新锐"),
    (2000, "🌳 精锐"),
    (8000, "🏔 王牌"),
    (30000, "👑 传奇"),
)
# 单支战队人数上限
SQUAD_MAX_MEMBERS = 20


def squad_tier(contribution: int) -> tuple[str, int, int]:
    """按总贡献计算战队等级。

    Args:
        contribution: 战队总贡献。

    Returns:
        ``(等级名, 当前档位下标, 下一档门槛)``；已满级时下一档门槛为 0。
    """
    total = max(0, _safe_int(contribution, 0))
    idx = 0
    for i, (threshold, _name) in enumerate(SQUAD_TIERS):
        if total >= threshold:
            idx = i
    name = SQUAD_TIERS[idx][1]
    next_threshold = SQUAD_TIERS[idx + 1][0] if idx + 1 < len(SQUAD_TIERS) else 0
    return name, idx, next_threshold


def squad_progress(contribution: int) -> tuple[str, int, int]:
    """返回战队等级的进度条文案。

    Args:
        contribution: 战队总贡献。

    Returns:
        ``(等级名, 当前进度, 下一档所需)``。
    """
    name, idx, nxt = squad_tier(contribution)
    total = max(0, _safe_int(contribution, 0))
    if not nxt:
        return name, total, 0
    base = SQUAD_TIERS[idx][0]
    return name, total - base, nxt - base


def squad_contribution(user: dict) -> int:
    """从用户档案推算他对战队的可贡献值。

    取值刻意只认「既有统计」，不引入新字段，这样老数据无需迁移。
    贡献 = 签到天数*2 + 抽奖次数 + 猜中次数 + 接龙次数 + 胜场。

    Args:
        user: 用户档案。

    Returns:
        贡献值。
    """
    return (
        _safe_int(user.get("total_sign"), 0) * 2
        + _safe_int(user.get("lottery_count"), 0)
        + _safe_int(user.get("guess_win"), 0)
        + _safe_int(user.get("chain_win"), 0)
        + _safe_int(user.get("rob_win"), 0)
        + _safe_int(user.get("tictactoe_win"), 0)
        + _safe_int(user.get("mine_clear"), 0)
    )

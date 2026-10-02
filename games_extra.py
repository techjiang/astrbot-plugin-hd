"""互动玩法的扩展纯逻辑层。

只放不依赖 AstrBot 运行时的算法与状态机，与 ``games.py`` 保持同样的设计约束：

- **无 IO、无全局可变状态**，随机性通过 ``rng`` 参数或确定性种子注入；
- 所有涉及积分的函数都保证「账目守恒」，返回值与余额变化严格一致；
- 数值边界先收敛再运算，脏数据不会炸。

覆盖：21 点、数字炸弹、猜谜、海龟汤、抢答、每日运势、称号/道具商店、
亲密度、礼物、表白、PK 对决、冷笑话等。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date

from .games import today_str


def _safe_int(value: object, default: int = 0) -> int:
    """把任意值安全转成非负 int，脏数据（None/字符串/负数）一律回退。

    Args:
        value: 原始值。
        default: 转换失败时的默认值。

    Returns:
        非负整数。
    """
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return max(0, default)


# --------------------------------------------------------------------- 21 点

# 黑杰克（起手两张凑成 21）赔付倍数，高于普通胜利
BLACKJACK_PAYOUT = 2.5
# 普通胜利赔付倍数
WIN_PAYOUT = 2.0
# 平局退回本金
PUSH_PAYOUT = 1.0


@dataclass
class BlackjackGame:
    """21 点对局状态。

    Attributes:
        player: 玩家手牌（1~13，1 视为 A）。
        dealer: 庄家手牌。
        bet: 本局下注积分。
        started_at: 开始时间戳。
        finished: 是否已结算。
        doubled: 是否已使用加倍。
    """

    player: list[int] = field(default_factory=list)
    dealer: list[int] = field(default_factory=list)
    bet: int = 0
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    doubled: bool = False

    # ---------------------------------------------------------- 牌值计算

    @staticmethod
    def card_value(cards: list[int]) -> int:
        """计算手牌点数，A 按 11 或 1 自动取舍最优值。

        Args:
            cards: 手牌点数列表（1~13）。

        Returns:
            不超过 21 时的最优点数；爆牌时返回最小可能点数。
        """
        total = 0
        aces = 0
        for card in cards:
            if card == 1:
                aces += 1
                total += 11
            else:
                total += min(card, 10)
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    @staticmethod
    def draw(rng: random.Random | None = None) -> int:
        """抽一张牌。

        Args:
            rng: 随机源，默认新建。

        Returns:
            1~13 的点数（1 为 A）。
        """
        rng = rng or random
        return rng.randint(1, 13)

    # ---------------------------------------------------------- 生命周期

    @classmethod
    def new(cls, bet: int, rng: random.Random | None = None) -> BlackjackGame:
        """开一局，各发两张牌；起手 21 点直接判为已结束。

        Args:
            bet: 下注积分（负数会被收敛为 0）。
            rng: 随机源。

        Returns:
            初始化后的对局。
        """
        game = cls(bet=max(0, int(bet)))
        game.player = [cls.draw(rng), cls.draw(rng)]
        game.dealer = [cls.draw(rng), cls.draw(rng)]
        if game.player_value() == 21:
            game.finished = True
        return game

    def player_value(self) -> int:
        """玩家当前点数。"""
        return self.card_value(self.player)

    def dealer_value(self) -> int:
        """庄家当前点数。"""
        return self.card_value(self.dealer)

    def is_blackjack(self) -> bool:
        """玩家是否起手黑杰克（两张牌 21 点）。"""
        return len(self.player) == 2 and self.player_value() == 21

    # ---------------------------------------------------------- 操作

    def hit(self, rng: random.Random | None = None) -> str:
        """玩家要牌。

        Args:
            rng: 随机源。

        Returns:
            ``continue`` 继续可操作；``bust`` 爆牌；
            ``win``/``lose``/``push`` 表示恰好 21 点触发的结算结果。
        """
        if self.finished:
            return "finished"
        self.player.append(self.draw(rng))
        if self.player_value() > 21:
            self.finished = True
            return "bust"
        if self.player_value() == 21:
            # 到达 21 点自动进入结算
            return self._settle(rng)
        return "continue"

    def stand(self, rng: random.Random | None = None) -> str:
        """玩家停牌，庄家补到 17 点以上再比大小。

        Args:
            rng: 随机源。

        Returns:
            ``win`` / ``lose`` / ``push``。
        """
        return self._settle(rng)

    def _settle(self, rng: random.Random | None = None) -> str:
        """结算对局。

        Args:
            rng: 随机源。

        Returns:
            ``win`` / ``lose`` / ``push``。
        """
        self.finished = True
        pv = self.player_value()
        if pv > 21:
            return "bust"
        while self.dealer_value() < 17:
            self.dealer.append(self.draw(rng))
        dv = self.dealer_value()
        if dv > 21 or pv > dv:
            return "win"
        if pv == dv:
            return "push"
        return "lose"

    def payout(self, result: str) -> int:
        """根据结算结果计算应返还的积分。

        Args:
            result: 结算结果。

        Returns:
            返还积分（含本金）；负数/爆牌为 0。
        """
        if result == "win":
            rate = BLACKJACK_PAYOUT if self.is_blackjack() else WIN_PAYOUT
            return int(self.bet * rate)
        if result == "push":
            return int(self.bet * PUSH_PAYOUT)
        return 0


# --------------------------------------------------------------------- 数字炸弹


@dataclass
class BombGame:
    """数字炸弹状态。

    区间为左闭右开 ``[low, high)``，``target`` 必落在区间内。

    Attributes:
        low: 当前下界（含）。
        high: 当前上界（不含）。
        target: 炸弹数字。
        started_at: 开始时间戳。
        last_user: 最后报数的人。
        rounds: 已报数次数。
    """

    low: int
    high: int
    target: int
    started_at: float = field(default_factory=time.time)
    last_user: str = ""
    rounds: int = 0

    @classmethod
    def new(cls, low: int, high: int, rng: random.Random | None = None) -> BombGame:
        """创建一个数字炸弹。

        Args:
            low: 无符号下界。
            high: 无符号上界（会被调整为至少比 low 大 10）。
            rng: 随机源。

        Returns:
            初始化后的游戏实例。
        """
        rng = rng or random
        lo = int(low)
        hi = int(high)
        if hi - lo < 10:
            hi = lo + 10
        return cls(low=lo, high=hi, target=rng.randint(lo, hi - 1))

    def report(self, value: int, user_id: str) -> tuple[bool, str]:
        """报一个数字。

        Args:
            value: 报出的数字。
            user_id: 报数者。

        Returns:
            ``(是否踩雷, 提示文案)``。
        """
        if value < self.low or value >= self.high:
            return False, f"请报 {self.low} ~ {self.high - 1} 之间的数字。"
        self.last_user = user_id
        self.rounds += 1
        if value == self.target:
            return True, f"💥 BOOM！{value} 就是炸弹，你把它引爆了！"
        if value < self.target:
            self.low = value + 1
        else:
            self.high = value
        return (
            False,
            f"安全！范围收窄为 {self.low} ~ {self.high - 1}，下一位继续。",
        )

    def span(self) -> int:
        """当前剩余区间宽度。"""
        return max(0, self.high - self.low)


# --------------------------------------------------------------------- 猜谜

# (谜面, 谜底, 备选提示)
RIDDLES: tuple[tuple[str, str, str], ...] = (
    ("什么东西越洗越脏？", "水", "每天都要用"),
    ("什么门永远关不上？", "球门", "和运动有关"),
    ("什么东西有头无脚？", "杯子", "桌上常见"),
    ("什么东西天气越热爬得越高？", "温度计", "和温度有关"),
    ("什么东西明明是你的，别人却用得比你还多？", "名字", "别人喊你时用"),
    ("什么东西越分享越多？", "快乐", "情绪类"),
    ("什么东西倒着走会变小？", "年龄", "和时间有关"),
    ("什么布剪不断？", "瀑布", "和自然有关"),
    ("什么东西你越给它，它越小？", "洞", "挖出来的"),
    ("什么东西没有翅膀却能飞？", "时间", "看不见摸不着"),
    ("什么东西有眼睛却看不见？", "针", "细细的"),
    ("什么车寸步难行？", "风车", "会转"),
    ("什么东西只能加不能减？", "年龄", "时间类"),
    ("什么东西人人都有，却没有一个人见过？", "名字", "称呼"),
    ("什么水永远用不完？", "泪水", "情绪相关"),
)


def random_riddle(rng: random.Random | None = None) -> tuple[str, str, str]:
    """随机取一条谜语。

    Args:
        rng: 随机源。

    Returns:
        ``(谜面, 谜底, 提示)``。
    """
    rng = rng or random
    return rng.choice(RIDDLES)


# --------------------------------------------------------------------- 海龟汤

# (标题, 汤面, 汤底)
TURTLE_SOUPS: tuple[tuple[str, str, str], ...] = (
    (
        "海龟汤",
        "一个男人走进餐厅，喝了一口海龟汤就哭了，然后默默离开。为什么？",
        "他曾与同伴海上遇难，同伴说抓到了海龟煮汤给他喝。"
        "他此刻喝到真正的海龟汤，才明白当年喝到的是同伴用自己的肉煮的汤——同伴早已不在。",
    ),
    (
        "电梯",
        "一个人住 20 楼，每天早上按 1 楼，晚上回家只按到 10 楼再走楼梯。为什么？",
        "他个子矮，够不到 20 楼的按钮；雨天带伞时可以按到。",
    ),
    (
        "合影",
        "一群人合影后看到照片都吓坏了，为什么？",
        "照片里少了一个人，而拍照的人是独自一人。",
    ),
    (
        "半根火柴",
        "沙漠中发现一具尸体，手里攥着半根火柴，周围没有脚印。他是怎么死的？",
        "他和同伴坐热气球遇险，靠抽火柴决定谁跳下去减重。他抽到了半根。",
    ),
    (
        "雨夜",
        "雨夜，男人在车里听到一段广播后，立刻停下车痛哭。为什么？",
        "广播说某航班失事无人生还，那正是他妻子乘坐的航班——他刚刚才从电台点歌给她。",
    ),
    (
        "买鞋",
        "一个盲人买了双鞋，第二天他自杀了。为什么？",
        "他一直以为妻子还活着。他去买鞋时，店员问他给谁买，他说给妻子；"
        "店员说「可你妻子已经……」——他才知道妻子早已去世，他一直活在自己的世界里。",
    ),
)


def random_soup(rng: random.Random | None = None) -> tuple[str, str, str]:
    """随机取一局海龟汤。

    Args:
        rng: 随机源。

    Returns:
        ``(标题, 汤面, 汤底)``。
    """
    rng = rng or random
    return rng.choice(TURTLE_SOUPS)


# --------------------------------------------------------------------- 抢答


@dataclass
class RushGame:
    """抢答小游戏状态（谁最先发出指定内容）。

    Attributes:
        keyword: 目标关键词。
        started_at: 开始时间戳。
        winner: 获胜者 ID（空表示还没人抢到）。
        reward: 奖励积分。
    """

    keyword: str
    started_at: float = field(default_factory=time.time)
    winner: str = ""
    reward: int = 0

    def expired(self, timeout: int, now: float | None = None) -> bool:
        """是否已超时。

        Args:
            timeout: 超时秒数。
            now: 当前时间戳。

        Returns:
            是否超时。
        """
        now = time.time() if now is None else now
        return now - self.started_at > max(1, int(timeout))

    def check(self, text: str, user_id: str, timeout: int) -> tuple[bool, str]:
        """检查一条消息是否抢到了第一。

        Args:
            text: 消息文本。
            user_id: 发送者。
            timeout: 超时秒数。

        Returns:
            ``(是否抢到, 提示)``。
        """
        if self.winner:
            return False, "已经有人抢到了。"
        if self.expired(timeout):
            return False, "抢答已超时。"
        if self.keyword and self.keyword not in text:
            return False, ""
        self.winner = user_id
        return True, f"🥇 恭喜你抢到第一，+{self.reward} 积分！"


# --------------------------------------------------------------------- 每日运势

_FORTUNE_LEVELS = (
    ("大吉", 5, "✨"),
    ("中吉", 4, "🌟"),
    ("小吉", 3, "🌤"),
    ("平", 2, "🍃"),
    ("小凶", 1, "🌧"),
)
_FORTUNE_ASPECTS = ("事业", "财运", "爱情", "健康", "人际", "学业", "手气", "桃花")
_FORTUNE_TIPS = (
    "今天适合主动出击，好运藏在细节里。",
    "低调行事，稳住节奏比抢跑更重要。",
    "会遇到一位贵人，记得礼貌回礼。",
    "适合整理桌面与心情，清爽能带来效率。",
    "少刷一会儿群，今天专注力是稀缺资源。",
    "冲动消费预警，看到数字先深呼吸三秒。",
    "记得多喝水，好运也需要体力支撑。",
    "有人悄悄在观察你，保持体面。",
    "今天的手气适合抽奖，但别上头。",
    "适合复盘，今天想通的事会影响后面很久。",
    "对群友好一点，善意的回报会绕回来。",
    "别急着下结论，今天的信息要再看一遍。",
)
_ZODIAC = ("鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪")


def daily_fortune(seed: str, day: str | None = None) -> dict:
    """生成每日运势（同一用户同一天结果稳定）。

    Args:
        seed: 稳定随机种子，通常为「会话 + 用户」。
        day: 日期字符串，默认今天。

    Returns:
        含 ``date``/``level``/``star``/``score``/``aspects``/``luck``/``tip`` 的字典。
    """
    day = day or today_str()
    rng = random.Random(f"{seed}@{day}")
    level, score, icon = rng.choice(_FORTUNE_LEVELS)
    return {
        "date": day,
        "level": level,
        "score": score,
        "star": icon * score + "☆" * (5 - score),
        "aspects": rng.sample(_FORTUNE_ASPECTS, k=3),
        "luck": rng.randint(1, 99),
        "tip": rng.choice(_FORTUNE_TIPS),
    }


def zodiac_of(day: date | None = None) -> str:
    """按年份推算生肖。

    Args:
        day: 日期，默认今天。

    Returns:
        生肖名称。
    """
    day = day or date.today()
    return _ZODIAC[(day.year - 4) % 12]


# --------------------------------------------------------------------- 商店

# 道具 ID -> (名称, 价格, 类型, 说明)
# 类型：title=称号、frame=头像框、consumable=消耗品
SHOP_ITEMS: dict[str, tuple[str, int, str, str]] = {
    "title_ouhuang": ("称号·欧皇", 300, "title", "抽奖稀有掉落，也可直接购买。"),
    "title_feizhou": ("称号·非酋", 50, "title", "自嘲专用，便宜大碗。"),
    "title_qunzhu": ("称号·群主", 500, "title", "心理安慰型称号，无任何特权。"),
    "title_dalao": ("称号·大佬", 800, "title", "进群自带 BGM。"),
    "frame_chosen": ("头像框·天选之人", 1200, "frame", "抽奖稀有掉落，绝版限量。"),
    "frame_sakura": ("头像框·樱花", 400, "frame", "温柔滤镜，说话自带春风。"),
    "item_lucky_charm": ("幸运符", 150, "consumable", "收藏品，纯好看。"),
    "item_coffee": ("赛博咖啡", 30, "consumable", "喝了会显示一句鼓励。"),
}

# 礼物名 -> (emoji, 单价, 基础亲密度加成)
GIFT_ITEMS: dict[str, tuple[str, int, int]] = {
    "星星": ("⭐", 10, 3),
    "花": ("🌹", 20, 5),
    "奶茶": ("🧋", 30, 8),
    "蛋糕": ("🎂", 60, 15),
    "戒指": ("💍", 520, 80),
}


def resolve_shop_item(query: str) -> tuple[str, str, int, str, str] | None:
    """按名称/ID 模糊匹配商店道具。

    Args:
        query: 用户输入。

    Returns:
        ``(道具ID, 名称, 价格, 类型, 说明)``；未命中返回 ``None``。
    """
    q = (query or "").strip()
    if not q:
        return None
    # 精确匹配 ID 或全名优先
    for item_id, (name, price, kind, desc) in SHOP_ITEMS.items():
        if q in (item_id, name):
            return item_id, name, price, kind, desc
    # 其次后缀匹配（用户常写「大佬」而不是「称号·大佬」）
    for item_id, (name, price, kind, desc) in SHOP_ITEMS.items():
        if name.endswith(q) or q in name:
            return item_id, name, price, kind, desc
    return None


def item_display_name(item_id: str) -> str:
    """道具 ID 转展示名。

    Args:
        item_id: 道具 ID。

    Returns:
        展示名；未知 ID 原样返回。
    """
    entry = SHOP_ITEMS.get(item_id)
    return entry[0] if entry else item_id


# --------------------------------------------------------------------- 亲密度

# 亲密度阈值 -> 关系称谓
RELATION_STAGES: tuple[tuple[int, str], ...] = (
    (0, "陌生人"),
    (20, "点头之交"),
    (60, "熟人"),
    (150, "朋友"),
    (300, "好友"),
    (600, "知己"),
    (1000, "挚友"),
    (2000, "灵魂伴侣"),
)


def relation_stage(points: int) -> str:
    """把亲密度换算成关系称谓。

    Args:
        points: 亲密度点数。

    Returns:
        关系称谓。
    """
    stage = RELATION_STAGES[0][1]
    for threshold, name in RELATION_STAGES:
        if points >= threshold:
            stage = name
    return stage


def intimacy_key(a: str, b: str) -> str:
    """生成无向亲密度键（两人共用同一个键）。

    Args:
        a: 用户 A。
        b: 用户 B。

    Returns:
        排序后用 ``|`` 连接的键。
    """
    return "|".join(sorted((str(a), str(b))))


def add_intimacy(bucket: dict, a: str, b: str, delta: int) -> tuple[int, str]:
    """在给定字典中累加亲密度。

    Args:
        bucket: 存放亲密度数据的字典（就地更新）。
        a: 用户 A。
        b: 用户 B。
        delta: 增量，可为负。

    Returns:
        ``(新点数, 关系称谓)``。
    """
    key = intimacy_key(a, b)
    points = max(0, min(99999, int(bucket.get(key, 0)) + int(delta)))
    bucket[key] = points
    return points, relation_stage(points)


def get_intimacy(bucket: dict, a: str, b: str) -> int:
    """读取两人亲密度。

    Args:
        bucket: 亲密度数据字典。
        a: 用户 A。
        b: 用户 B。

    Returns:
        亲密度点数。
    """
    return int(bucket.get(intimacy_key(a, b), 0))


# --------------------------------------------------------------------- 表白 / 对决

_CONFESS_TEMPLATES = (
    "{a} 对 {b} 说：你是我这片宇宙里唯一的常量。",
    "{a} 对 {b} 说：想和你一起把普通的日子过成节日。",
    "{a} 对 {b} 说：我的信号塔只为你亮灯。",
    "{a} 对 {b} 说：要不要一起把后半生的 bug 都调成 feature？",
    "{a} 对 {b} 说：今天的心动检测到异常，源头在你。",
    "{a} 对 {b} 说：别人是过客，你是我的常驻进程。",
    "{a} 对 {b} 说：我这一生的版本迭代，都为了兼容你。",
)


def confess_line(a: str, b: str, rng: random.Random | None = None) -> str:
    """生成一句表白文案。

    Args:
        a: 表白方昵称。
        b: 被表白方昵称。
        rng: 随机源。

    Returns:
        文案。
    """
    rng = rng or random
    return rng.choice(_CONFESS_TEMPLATES).format(a=a, b=b)


def duel_power(user: dict, rng: random.Random | None = None) -> int:
    """计算对决战力。

    战力 = 余额 × 0.3 + 胜场 × 20 + 随机扰动(0~60)。

    Args:
        user: 用户档案。
        rng: 随机源。

    Returns:
        战力值（非负整数）。
    """
    rng = rng or random
    return int(
        _safe_int(user.get("balance")) * 0.3
        + _safe_int(user.get("duel_win")) * 20
        + rng.randint(0, 60)
    )


def duel(a: dict, b: dict, rng: random.Random | None = None) -> tuple[str, int, str]:
    """判定一次 PK 结果。

    Args:
        a: 挑战者档案。
        b: 应战者档案。
        rng: 随机源。

    Returns:
        ``(胜者, 战力差, 战报文案)``，胜者为 ``"a"``/``"b"``/``"draw"``。
    """
    rng = rng or random
    pa = duel_power(a, rng)
    pb = duel_power(b, rng)
    diff = abs(pa - pb)
    if diff < 5:
        return "draw", diff, "两人势均力敌，打成平手，观众表示值回票价。"
    winner = "a" if pa > pb else "b"
    flavor = rng.choice(
        ("一记漂亮的连招拿下胜利！", "稳扎稳打，步步为营。", "抓住破绽，一击致命！")
    )
    return winner, diff, flavor


# --------------------------------------------------------------------- 冷笑话

JOKES: tuple[str, ...] = (
    "程序员的浪漫：我把你和我的关系写成了常量，因为我不想它变。",
    "为什么程序员总分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。",
    "老板：这个 bug 多久能修好？我：已经修好了，只是它变成了两个。",
    "问：如何让一个人瞬间安静？答：把他的 WiFi 拔了。",
    "我的人生就像 AI 生成：看起来有模有样，细看全是幻觉。",
    "今天我要早睡——说完这句话我就又熬到了凌晨三点。",
    "群友说：我请你吃饭。我说：你先把这句话撤回。",
    "减肥第一天：吃了三顿饭来庆祝这个伟大的开始。",
    "有人问我为什么不谈恋爱，我说我在等一个不需要解释的人。",
    "我的存款和我的头发一样，都在以肉眼可见的速度减少。",
)


def random_joke(rng: random.Random | None = None) -> str:
    """随机取一条冷笑话。

    Args:
        rng: 随机源。

    Returns:
        笑话文本。
    """
    rng = rng or random
    return rng.choice(JOKES)

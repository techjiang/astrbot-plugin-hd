"""互动玩法的第三批扩展：**世界线 / 赛季 / 协作** 类高级玩法（纯逻辑层）。

与 ``games.py`` / ``games_extra.py`` / ``games_plus.py`` / ``games_tags.py``
共用同一套设计约束：无 IO、无全局可变状态、随机靠 ``rng`` 注入、
数值边界先收敛再运算。

本模块覆盖三类此前缺失的「长线玩法」：

1. **群 Boss 战**（:class:`BossFight`）—— 把「签到领分」的单人循环，
   升级成全群一起打的协作目标。伤害按投入积分折算，奖励只在击杀时结算，
   因此它的经济模型是「先付费、按贡献分配」，**不存在白拿**。
2. **赛季**（:func:`season_of` / :func:`season_reward`）—— 给长跑一个明确
   的终点，按自然月分赛季，把「本季积分增量」而非「总积分」作为排名依据，
   避免老玩家永久霸榜。
3. **养成**（:func:`pet_gain` / :class:`PetState`）—— 宠物 / 家园这类
   需要每日照料的轻养成，产出**严格小于**照料成本折算收益，只作为情绪价值
   与标签来源，不做印钞机。

经济铁律（与 ``games_plus`` 一致）：
    - 任何函数的返回值与「调用方余额变化」必须严格对得上；
    - 期望为正的随机玩法一律不允许存在，测试里有守恒断言。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .games import _safe_int

# --------------------------------------------------------------------- 通用


def _clamp(value: object, lo: int, hi: int, default: int = 0) -> int:
    """把任意值收敛到 ``[lo, hi]``。

    Args:
        value: 原始值。
        lo: 下界。
        hi: 上界。
        default: 转换失败的回退值。

    Returns:
        收敛后的整数。
    """
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        result = default
    return min(max(result, lo), hi)


# --------------------------------------------------------------------- 群 Boss 战

# Boss 模板：(码, 名称, 总血量, 每点伤害所需积分, 击杀奖池, 描述)。
# 「每点伤害所需积分」= 玩家花多少积分换 1 点伤害，用于把投入折算成贡献。
# 「击杀奖池」按伤害占比分配，且奖池总额**小于**全员总投入 —— 合作必然有损耗，
# 所以刷 Boss 不能造分（tests/test_world.py 有守恒断言）。
BOSSES: tuple[tuple[str, str, int, int, int, str], ...] = (
    (
        "slime",
        "史莱姆王",
        1200,
        1,
        700,
        "果冻质感，打起来手感很好，就是有点黏。",
    ),
    (
        "dragon",
        "烈焰巨龙",
        6000,
        1,
        3200,
        "翅膀一扇，群里温度都高了两度。",
    ),
    (
        "kraken",
        "深海巨妖",
        18000,
        1,
        8800,
        "据说它的触手能同时打十个群。",
    ),
    (
        "leviathan",
        "虚空利维坦",
        60000,
        1,
        26000,
        "没人知道它为什么出现，包括它自己。",
    ),
)
BOSS_MAP: dict[str, tuple[str, int, int, int, str]] = {
    code: (name, hp, cost, pool, desc) for code, name, hp, cost, pool, desc in BOSSES
}


def boss_damage(spend: int, cost: int = 1) -> int:
    """把投入的积分折算成伤害。

    Args:
        spend: 投入积分。
        cost: 每点伤害所需积分（至少 1）。

    Returns:
        伤害值；``spend <= 0`` 时返回 0。
    """
    paid = max(0, _safe_int(spend, 0))
    rate = max(1, _safe_int(cost, 1))
    return paid // rate


def boss_share(damages: dict[str, int], pool: int) -> dict[str, int]:
    """按伤害占比分配奖池，余数给伤害最高者。

    刻意**不做四舍五入**而是向下取整后把余数补给第一名，好处是
    ``Σ 分配额 == pool`` 恒成立（不会因为取整凭空多出或少掉积分）。

    Args:
        damages: ``{用户: 伤害}``。
        pool: 奖池总额。

    Returns:
        ``{用户: 分得积分}``；总伤害为 0 时返回空字典。
    """
    total = sum(max(0, _safe_int(v, 0)) for v in damages.values())
    total_pool = max(0, _safe_int(pool, 0))
    if total <= 0 or total_pool <= 0 or not damages:
        return {}
    result: dict[str, int] = {}
    for uid, value in damages.items():
        result[uid] = total_pool * max(0, _safe_int(value, 0)) // total
    remainder = total_pool - sum(result.values())
    if remainder:
        top = max(damages, key=lambda k: (max(0, _safe_int(damages[k], 0)), k))
        result[top] = result.get(top, 0) + remainder
    return result


# --------------------------------------------------------------------- 战力战斗（v1.4.6）

# 战斗模型：**不花积分，只看战力**。
#
# 老模型是「投入积分换伤害」：Boss 血量 1200，奖池只有 700，全群合力打完
# 必然净亏 500。对玩家来说这是「先交钱、再按贡献分回一部分」，投入产出完全
# 不成正比，没人愿意碰。更根本的问题是它和已有的「战力」体系毫无关联。
#
# 新模型把 Boss 战变成「战力 → 伤害」的纯战力玩法：
#
#   单次伤害 = 战力 × 浮动(±BATTLE_SWING) × 克制系数
#
# - 不扣任何积分，参战成本为零，缺勤才有成本（Boss 会回血）；
# - 战力越高伤害越高，战力第一次有了实际用途；
# - 克制系数让「选对 Boss 上」有意义，而不是无脑刷；
# - 击杀后全群发放赏金 + 每人记一次击杀功勋（战力来源之一）。
#
# 经济上唯一需要守住的约束是「发放 ≤ 上限」，见 :func:`battle_reward`。

# 单次攻击的战力浮动区间（上下各 15%），让同一场里也有悬念
BATTLE_SWING = 0.15
# 克制系数：用对打法伤害提升，用错略微衰减（平均仍是 1.0，不额外送钱）
BATTLE_COUNTER_UP = 1.3
BATTLE_COUNTER_DOWN = 0.85
# 每位参与者能领到的赏金占「击杀赏金」的比例上限，以及全场总发放的倍数上限。
# 这两个上限保证「赏金 × 参战人数」不会超过一次击杀应得的总量。
BATTLE_REWARD_SHARE = 0.25
BATTLE_REWARD_TOTAL_MULT = 2.0
# 战力一次攻击至少造成 1 点伤害，避免新号完全打不动
BATTLE_MIN_HIT = 1

# 打法（克制）与 Boss 的对应关系：``打法 -> 克制的 Boss 码``。
# 玩家用「攻击 强攻」指定打法，命中克制关系时伤害 ×1.3。
BATTLE_STYLES: tuple[tuple[str, str, str], ...] = (
    ("强攻", "burst", "slime"),
    ("poke", "poke", "kraken"),
    ("breach", "破甲", "dragon"),
    ("seal", "封印", "leviathan"),
)

# Boss → 打法 的默认最优解，用来自动推荐
BOSS_WEAKNESS: dict[str, str] = {
    "slime": "强攻",
    "dragon": "破甲",
    "kraken": "消耗",
    "leviathan": "封印",
}


def battle_style(text: str) -> str:
    """把玩家输入的写法归一成标准打法名。

    Args:
        text: 用户输入（可为空）。

    Returns:
        标准打法名（强攻 / 消耗 / 破甲 / 封印），无法识别时返回空串。
    """
    want = "".join((text or "").split()).lower()
    if not want:
        return ""
    for name, code, _counter in BATTLE_STYLES:
        if want in (name, code):
            return name
    return ""


def battle_counter_multiplier(style: str, boss_code: str) -> float:
    """计算打法对 Boss 的克制系数。

    Args:
        style: 打法名（空串表示随便打）。
        boss_code: Boss 码。

    Returns:
        克制系数：克制 1.3、被克 0.85、其余 1.0。
    """
    if not style:
        return 1.0
    weak = BOSS_WEAKNESS.get(boss_code, "")
    if not weak:
        return 1.0
    if style == weak:
        return BATTLE_COUNTER_UP
    if style == BOSS_WEAKNESS.get(weak, ""):
        return BATTLE_COUNTER_DOWN
    # 非克制也非被克：仍按 1.0 结算，避免「随便打」变成惩罚性玩法
    return 1.0


def battle_damage(
    power: int,
    boss_code: str,
    style: str = "",
    rng: random.Random | None = None,
) -> tuple[int, float]:
    """按战力计算一次攻击的伤害。

    Args:
        power: 玩家战力（由 ``games_plus.power_of`` 算出）。
        boss_code: Boss 码。
        style: 打法名，空串表示不指定。
        rng: 随机源。

    Returns:
        ``(伤害, 本次实际使用的倍率)``。
    """
    rng = rng or random
    base = max(0, _safe_int(power, 0))
    swing = 1.0 + rng.uniform(-BATTLE_SWING, BATTLE_SWING)
    multiplier = swing * battle_counter_multiplier(style, boss_code)
    damage = int(base * multiplier)
    # 战力为 0 的新号也至少打 1 点，不然「参战」这件事就毫无意义
    return max(BATTLE_MIN_HIT, damage), multiplier


def battle_reward(pool: int, damages: dict[str, int], people: int) -> int:
    """计算一次击杀后**每位参与者**可领的赏金。

    赏金是这次击杀唯一会「凭空产生」的积分，所以必须收敛：既不能让伤害榜
    变成印钞机（一次击杀发几千），也不能让几十人围观时单人白拿全额。

    Args:
        pool: Boss 模板里配置的击杀赏金。
        damages: ``{用户: 累计伤害}``，用于按贡献决定谁有资格拿。
        people: 本次赏金实际发放人数。

    Returns:
        每人赏金；无人参战时返回 0。
    """
    total_pool = max(0, _safe_int(pool, 0))
    count = max(0, _safe_int(people, 0))
    active = sum(1 for v in damages.values() if _safe_int(v, 0) > 0)
    if total_pool <= 0 or count <= 0 or active <= 0:
        return 0
    per_person_cap = int(total_pool * BATTLE_REWARD_SHARE)
    total_cap = int(total_pool * BATTLE_REWARD_TOTAL_MULT)
    per_person = min(per_person_cap, total_cap // count)
    return max(0, per_person)


@dataclass
class BossFight:
    """一局群 Boss 战（纯战力玩法，不消耗积分）。

    Attributes:
        code: Boss 码。
        name: Boss 名称。
        hp_max: 总血量。
        hp: 剩余血量。
        damages: ``{用户: 累计伤害}``。
        attacks: ``{用户: 出手次数}``，用于展示活跃度。
        pool: 击杀赏金。
        started_at: 开始时间戳。
        finished: 是否已结束。
        killed_by: 最后一击的玩家（未击杀为空）。
        styles: ``{用户: 最近一次使用的打法}``，用于战报点出「克制」。
    """

    code: str
    name: str
    hp_max: int
    hp: int
    # cost 仅为兼容旧存档保留：新模型不扣积分，恒为 0
    cost: int = 0
    pool: int = 0
    damages: dict[str, int] = field(default_factory=dict)
    attacks: dict[str, int] = field(default_factory=dict)
    started_at: int = 0
    finished: bool = False
    killed_by: str = ""
    styles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, code: str, started_at: int = 0) -> BossFight:
        """按模板开一局。

        Args:
            code: Boss 码，未知码回退到第一个模板。
            started_at: 开始时间戳。

        Returns:
            新的对局对象。
        """
        entry = BOSS_MAP.get(code) or BOSS_MAP[BOSSES[0][0]]
        name, hp, _cost, pool, _desc = entry
        return cls(
            code=code if code in BOSS_MAP else BOSSES[0][0],
            name=name,
            hp_max=hp,
            hp=hp,
            cost=0,
            pool=pool,
            started_at=max(0, _safe_int(started_at, 0)),
        )

    @property
    def progress(self) -> int:
        """进度百分比（0~100）。"""
        if self.hp_max <= 0:
            return 100
        return min(100, max(0, (self.hp_max - self.hp) * 100 // self.hp_max))

    @property
    def total_damage(self) -> int:
        """累计总伤害。"""
        return sum(max(0, _safe_int(v, 0)) for v in self.damages.values())

    # 兼容旧字段名：老存档/老测试里用 ``spenders`` 读「投入了多少」，
    # 新模型里它就是伤害（战力打法没有投入概念）。
    @property
    def spenders(self) -> dict[str, int]:
        """兼容别名：新模型下等于累计伤害。"""
        return dict(self.damages)

    def attendance(self) -> int:
        """有效参战人数（伤害 > 0）。"""
        return sum(1 for v in self.damages.values() if _safe_int(v, 0) > 0)

    def weak_style(self) -> str:
        """本 Boss 被哪种打法克制。"""
        return BOSS_WEAKNESS.get(self.code, "")

    def attack(self, uid: str, damage: int, style: str = "") -> tuple[int, int, bool]:
        """对 Boss 发起一次攻击（伤害由调用方按战力算好）。

        Args:
            uid: 用户 ID。
            damage: 本次伤害。
            style: 本次使用的打法。

        Returns:
            ``(实际伤害, 剩余血量, 是否本次击杀)``；对局已结束时返回 ``(0, hp, False)``。
        """
        if self.finished:
            return 0, self.hp, False
        want = max(0, _safe_int(damage, 0))
        if want <= 0:
            return 0, self.hp, False
        dealt = min(want, self.hp)
        # 记**实际打出**的伤害：Boss 只剩最后一点血时，请求 300 点却只能打出
        # 20 点，若按请求值记功，贡献占比会虚高。
        self.damages[uid] = self.damages.get(uid, 0) + dealt
        self.attacks[uid] = self.attacks.get(uid, 0) + 1
        if style:
            self.styles[uid] = style
        self.hp -= dealt
        if self.hp <= 0:
            self.hp = 0
            self.finished = True
            self.killed_by = uid
            return dealt, 0, True
        return dealt, self.hp, False

    def settle(self) -> tuple[dict[str, int], str]:
        """结算赏金。

        Returns:
            ``({用户: 分得积分}, 击杀者)``；未击杀时返回空字典。
        """
        if not self.finished:
            return {}, ""
        return boss_share(self.damages, self.pool), self.killed_by

    def render(self) -> str:
        """渲染对局进度。

        Returns:
            形如 ``🐲 烈焰巨龙 62% [██████░░░░] 2280/6000（弱点：破甲）`` 的文本。
        """
        from .games import bar

        weak = self.weak_style()
        tail = f"（弱点：{weak}）" if weak else ""
        return (
            f"🐲 {self.name} {self.progress}% "
            f"{bar(self.hp_max - self.hp, self.hp_max)} {self.hp}/{self.hp_max}{tail}"
        )


# --------------------------------------------------------------------- 赛季

# 赛季以自然月为周期：``2026-03``。奖励按「本季增量」分档。
SEASON_TIERS: tuple[tuple[int, str, int], ...] = (
    (0, "青铜", 30),
    (500, "白银", 120),
    (2000, "黄金", 400),
    (8000, "铂金", 1200),
    (30000, "钻石", 3000),
    (100000, "王者", 8888),
)


# 档位名到默认阈值的映射，供配置覆盖时复用（保持名字稳定，只让管理员改数值）
SEASON_TIER_NAMES = tuple(name for _threshold, name, _reward in SEASON_TIERS)

# 赛季奖励的硬上限。档位奖励来自配置，配错一个数量级就会变成印钞机，
# 这里给个天花板兜住（足够大，正常运营碰不到）。
SEASON_REWARD_MAX = 1_000_000
SEASON_THRESHOLD_MAX = 100_000_000


def season_tiers(
    raw: object = None,
) -> list[tuple[int, str, int]]:
    """把配置里的赛季档位规整成 ``[(阈值, 段位名, 奖励), ...]``。

    管理员可能在 WebUI 里把阈值填成倒序、负数、甚至只填一半。
    这里统一处理：

    - 阈值收敛到 ``0 ~ 1e8`` 并对**升序排序**（段位必须单调）；
    - 奖励收敛到 ``0 ~ 1e6``；
    - 名称为空时用内置档位名补齐；
    - 解析不出任何有效档位时回退到内置 ``SEASON_TIERS``。

    Args:
        raw: 配置值（列表，条目含 ``threshold`` / ``name`` / ``reward``）。

    Returns:
        升序且至少一项的档位列表。
    """
    fallback = list(SEASON_TIERS)
    if not isinstance(raw, list):
        return fallback
    tiers: list[tuple[int, str, int]] = []
    meaningful = False
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        threshold = _clamp(
            entry.get("threshold"), 0, SEASON_THRESHOLD_MAX, index * 1000
        )
        reward = _clamp(entry.get("reward"), 0, SEASON_REWARD_MAX, 0)
        name = str(entry.get("name") or "").strip()
        if not name:
            pos = min(index, len(SEASON_TIER_NAMES) - 1)
            name = SEASON_TIER_NAMES[pos]
        # 「门槛和奖励都被写成脏值」的条目说明这行是废的：
        # 收它会让整张表退化成一个 0 门槛 0 奖励的假档位，
        # 玩家攒再多分也领不到奖励（比直接回退默认值糟糕得多）。
        if (
            _clamp(entry.get("threshold"), -1, SEASON_THRESHOLD_MAX, -1) >= 0
            or _clamp(entry.get("reward"), -1, SEASON_REWARD_MAX, -1) >= 0
        ):
            meaningful = True
        tiers.append((threshold, name, reward))
    if not tiers or not meaningful:
        return fallback
    tiers.sort(key=lambda row: row[0])
    # 同名档位会让「当前段位」显示得莫名其妙，顺手去重（保留先出现的）
    seen: set[str] = set()
    deduped: list[tuple[int, str, int]] = []
    for row in tiers:
        if row[1] in seen:
            continue
        seen.add(row[1])
        deduped.append(row)
    return deduped or fallback


def season_of(day: str) -> str:
    """由日期推出赛季标识。

    Args:
        day: 形如 ``2026-03-15`` 的日期串。

    Returns:
        形如 ``2026-03`` 的赛季标识；解析失败时回退为 ``未知赛季``。
    """
    text = (day or "").strip()
    parts = text.split("-")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return "未知赛季"
    return f"{parts[0]}-{int(parts[1]):02d}"


def normalize_tiers(
    tiers: object = None,
) -> list[tuple[int, str, int]]:
    """把外部传入的档位表收敛成引擎能安全比较的形态。

    调用方既可能传 ``season_tiers()`` 的结果，也可能直接把配置里的
    ``[{"threshold": ...}]`` 原样塞进来。后者如果混进字符串阈值，
    排序与比较都会抛 ``TypeError`` —— 一次配置手滑不该让指令崩掉。

    Args:
        tiers: 档位表（已规整的元组列表，或原始配置列表）。

    Returns:
        升序的 ``[(阈值, 段位名, 奖励), ...]``。
    """
    if not tiers:
        return list(SEASON_TIERS)
    if all(isinstance(row, (list, tuple)) and len(row) >= 3 for row in tiers):
        rows = [
            (
                _clamp(row[0], 0, SEASON_THRESHOLD_MAX, 0),
                str(row[1]),
                _clamp(row[2], 0, SEASON_REWARD_MAX, 0),
            )
            for row in tiers  # type: ignore[union-attr]
            if isinstance(row, (list, tuple))
        ]
        rows.sort(key=lambda row: row[0])
        return rows or list(SEASON_TIERS)
    return season_tiers(tiers)


def season_tier(
    gain: int, tiers: list[tuple[int, str, int]] | None = None
) -> tuple[int, str, int]:
    """按本季积分增量定位段位。

    Args:
        gain: 本季积分增量（负值按 0 处理）。
        tiers: 档位表，默认内置 ``SEASON_TIERS``；运行期传入管理员配置。

    Returns:
        ``(最低增量, 段位名, 奖励积分)``。
    """
    table = normalize_tiers(tiers)
    value = max(0, _safe_int(gain, 0))
    tier = table[0]
    for entry in table:
        if value >= entry[0]:
            tier = entry
        else:
            break
    return tier


def season_reward(gain: int, tiers: list[tuple[int, str, int]] | None = None) -> int:
    """计算赛季结算奖励。

    Args:
        gain: 本季积分增量。
        tiers: 档位表，默认内置。

    Returns:
        奖励积分。
    """
    return season_tier(gain, tiers)[2]


def season_progress(
    gain: int, tiers: list[tuple[int, str, int]] | None = None
) -> tuple[str, str, int]:
    """计算距离下一段位还差多少。

    Args:
        gain: 本季积分增量。
        tiers: 档位表，默认内置。

    Returns:
        ``(当前段位, 下一段位或 "已封顶", 还差多少)``。
    """
    table = normalize_tiers(tiers)
    value = max(0, _safe_int(gain, 0))
    current = season_tier(value, table)[1]
    for threshold, name, _reward in table:
        if value < threshold:
            return current, name, threshold - value
    return current, "已封顶", 0


def season_pending(
    gain: int,
    tiers: list[tuple[int, str, int]] | None = None,
) -> list[tuple[int, str, int]]:
    """列出「按历史最高段位」仍可补领的档位。

    赛季奖励是**逐档解锁**而不是「总余额式已领」：老实现用一个
    ``season_reward`` 字段记住「上次领到多少奖励」，于是管理员把档位奖励
    上调后，玩家会因为「奖励总额看起来变多了」而莫名其妙地能再领一次；
    下调后又会永远领不到。改成按「已解锁的最高档位序号」记账，
    行为就与档位数值解耦了。

    Args:
        gain: 本季积分增量。
        tiers: 档位表，默认内置。

    Returns:
        可补领的 ``[(序号, 段位名, 奖励), ...]``，按档位升序。
    """
    table = normalize_tiers(tiers)
    value = max(0, _safe_int(gain, 0))
    rows: list[tuple[int, str, int]] = []
    for index, (threshold, name, reward) in enumerate(table, 1):
        if value >= threshold:
            rows.append((index, name, reward))
    return rows


# --------------------------------------------------------------------- 养成（宠物 / 家园）

# 宠物阶段：(码, 名称, 所需亲密度(照料次数), 每日产出加成)
PET_STAGES: tuple[tuple[str, str, int, int], ...] = (
    ("egg", "蛋", 0, 1),
    ("baby", "幼体", 5, 2),
    ("child", "幼崽", 20, 3),
    ("adult", "成年", 60, 5),
    ("elder", "长老", 150, 7),
)
PET_MAP: dict[str, tuple[str, int, int]] = {
    code: (name, need, bonus) for code, name, need, bonus in PET_STAGES
}
# 照料的冷却窗口（秒）：一天最多有效照料一次，防止刷亲密度
PET_FEED_COOLDOWN = 20 * 3600
# 单次照料的产出上限（积分）。故意设得很低 —— 养成的价值在「情绪」不在「收益」。
PET_MAX_GAIN = 6


def pet_stage(care: int) -> tuple[str, str, int]:
    """按照料次数定位宠物阶段。

    Args:
        care: 累计照料次数。

    Returns:
        ``(阶段码, 阶段名, 每日产出加成)``。
    """
    value = max(0, _safe_int(care, 0))
    stage = PET_STAGES[0]
    for entry in PET_STAGES:
        if value >= entry[2]:
            stage = entry
        else:
            break
    return stage[0], stage[1], stage[3]


def pet_need(care: int) -> tuple[str, int]:
    """距离下一阶段还差多少次照料。

    Args:
        care: 累计照料次数。

    Returns:
        ``(下一阶段名或 "已封顶", 还差次数)``。
    """
    value = max(0, _safe_int(care, 0))
    for _code, name, need, _bonus in PET_STAGES:
        if value < need:
            return name, need - value
    return "已封顶", 0


def pet_gain(care: int, rng: random.Random | None = None) -> int:
    """一次照料的产出积分。

    产出严格约束在 ``[0, PET_MAX_GAIN]``，且与阶段正相关 —— 但即使到最高阶段
    （长老 +7 加成），单次期望也只有约 6 分，远低于一次签到的下限，
    因此它不构成任何刷分路径，只作为养成正反馈存在。

    Args:
        care: 累计照料次数。
        rng: 随机源。

    Returns:
        产出积分。
    """
    rng = rng or random
    _code, _name, bonus = pet_stage(care)
    ceiling = min(PET_MAX_GAIN, max(0, bonus))
    if ceiling <= 0:
        return 0
    return int(rng.randint(1, ceiling))


@dataclass
class PetState:
    """宠物的持久化状态（存在用户档案的 ``pet`` 字段里）。

    Attributes:
        name: 宠物名。
        care: 累计照料次数。
        fed_at: 上次照料时间戳。
        born: 领养时间戳。
    """

    name: str = ""
    care: int = 0
    fed_at: int = 0
    born: int = 0

    @classmethod
    def from_dict(cls, raw: object) -> PetState:
        """从用户档案里的原始值还原状态。

        Args:
            raw: 可能是字典、``None`` 或脏数据。

        Returns:
            安全的 :class:`PetState`（永远可写）。
        """
        if not isinstance(raw, dict):
            return cls()
        return cls(
            name=str(raw.get("name") or "")[:24],
            care=max(0, _safe_int(raw.get("care", 0), 0)),
            fed_at=max(0, _safe_int(raw.get("fed_at", 0), 0)),
            born=max(0, _safe_int(raw.get("born", 0), 0)),
        )

    def to_dict(self) -> dict[str, object]:
        """转回可 JSON 序列化的字典。

        Returns:
            字典形式。
        """
        return {
            "name": self.name,
            "care": self.care,
            "fed_at": self.fed_at,
            "born": self.born,
        }

    def describe(self) -> str:
        """渲染状态描述。

        Returns:
            多行文本。
        """
        code, stage, bonus = pet_stage(self.care)
        next_name, remain = pet_need(self.care)
        tail = f"距离「{next_name}」还差 {remain} 次照料" if remain else "已是最终形态"
        return (
            f"🐣 {self.name or '未命名'} · {stage}\n"
            f"照料 {self.care} 次｜每日产出 +{bonus}\n"
            f"{tail}"
        )

    def next_feed_at(self, now: int) -> int:
        """下一次可照料的时间戳。

        Args:
            now: 当前时间戳。

        Returns:
            可照料的时间戳；立即可用返回 ``now``。
        """
        ready = self.fed_at + PET_FEED_COOLDOWN
        return max(int(now), ready)

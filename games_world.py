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


@dataclass
class BossFight:
    """一局群 Boss 战。

    Attributes:
        code: Boss 码。
        name: Boss 名称。
        hp_max: 总血量。
        hp: 剩余血量。
        damages: ``{用户: 累计伤害}``。
        spenders: ``{用户: 累计投入积分}``，用于回报「你投入了多少」。
        started_at: 开始时间戳。
        finished: 是否已结束。
        killed_by: 最后一击的玩家（未击杀为空）。
    """

    code: str
    name: str
    hp_max: int
    hp: int
    cost: int = 1
    pool: int = 0
    damages: dict[str, int] = field(default_factory=dict)
    spenders: dict[str, int] = field(default_factory=dict)
    started_at: int = 0
    finished: bool = False
    killed_by: str = ""

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
        name, hp, cost, pool, _desc = entry
        return cls(
            code=code if code in BOSS_MAP else BOSSES[0][0],
            name=name,
            hp_max=hp,
            hp=hp,
            cost=cost,
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
        return sum(self.damages.values())

    def attack(self, uid: str, spend: int) -> tuple[int, int, bool]:
        """对 Boss 发起一次攻击。

        Args:
            uid: 用户 ID。
            spend: 投入积分（由调用方先行扣除）。

        Returns:
            ``(实际伤害, 剩余血量, 是否本次击杀)``；对局已结束时返回 ``(0, hp, False)``。
        """
        if self.finished:
            return 0, self.hp, False
        damage = boss_damage(spend, self.cost)
        if damage <= 0:
            return 0, self.hp, False
        dealt = min(damage, self.hp)
        # 记录**实际打出**的伤害而不是请求值：Boss 只剩最后一点血时，
        # 请求 1500 点却只能打出 700 点，若按请求值记功，
        # 贡献占比与「你投入了多少」都会虚高，还会误导玩家以为被多扣了。
        self.damages[uid] = self.damages.get(uid, 0) + dealt
        self.spenders[uid] = self.spenders.get(uid, 0) + dealt * max(1, self.cost)
        self.hp -= dealt
        if self.hp <= 0:
            self.hp = 0
            self.finished = True
            self.killed_by = uid
            return dealt, 0, True
        return dealt, self.hp, False

    def settle(self) -> tuple[dict[str, int], str]:
        """结算奖池。

        Returns:
            ``({用户: 分得积分}, 击杀者)``；未击杀时返回空字典。
        """
        if not self.finished:
            return {}, ""
        return boss_share(self.damages, self.pool), self.killed_by

    def render(self) -> str:
        """渲染对局进度。

        Returns:
            形如 ``🐲 烈焰巨龙 Lv.1 62% [██████░░░░] 2280/6000`` 的文本。
        """
        from .games import bar

        return (
            f"🐲 {self.name} {self.progress}% "
            f"{bar(self.hp_max - self.hp, self.hp_max)} {self.hp}/{self.hp_max}"
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


def season_tier(gain: int) -> tuple[int, str, int]:
    """按本季积分增量定位段位。

    Args:
        gain: 本季积分增量（负值按 0 处理）。

    Returns:
        ``(最低增量, 段位名, 奖励积分)``。
    """
    value = max(0, _safe_int(gain, 0))
    tier = SEASON_TIERS[0]
    for entry in SEASON_TIERS:
        if value >= entry[0]:
            tier = entry
        else:
            break
    return tier


def season_reward(gain: int) -> int:
    """计算赛季结算奖励。

    Args:
        gain: 本季积分增量。

    Returns:
        奖励积分。
    """
    return season_tier(gain)[2]


def season_progress(gain: int) -> tuple[str, str, int]:
    """计算距离下一段位还差多少。

    Args:
        gain: 本季积分增量。

    Returns:
        ``(当前段位, 下一段位或 "已封顶", 还差多少)``。
    """
    value = max(0, _safe_int(gain, 0))
    current = season_tier(value)[1]
    for threshold, name, _reward in SEASON_TIERS:
        if value < threshold:
            return current, name, threshold - value
    return current, "已封顶", 0


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

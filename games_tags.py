"""互动玩法的标签体系：成就徽章的「分类 + 稀有度 + 标签检索」。

本模块是纯逻辑层，与 ``games.py`` / ``games_extra.py`` / ``games_plus.py``
共用同一套设计约束：无 IO、无全局可变状态、随机性靠 ``rng`` 注入。

它解决的是同一个数据被反复问的问题：**「我有什么、这些算什么级别、
还有哪些没拿、怎么搜到某个标签」**。原始实现里这几件事散落在
``cmd_achievements`` 里用字符串拼接完成，导致：

- 无法按稀有度或分类筛选，「还差什么」只能全量翻页；
- 昵称与成就码不做归一化，「成就 签到」查不到「签到七日」；
- 稀有度是拍脑袋写死在文案里的，改数值时文案不会跟着变。

因此这里把「标签」抽成一等公民：每个成就带分类、稀有度、进度与解锁时间，
并可被搜索、筛选、按稀有度排序。稀有度**由阈值推导**而非写死，
所以调整成就阈值后展示会自动同步，不会说谎。
"""

from __future__ import annotations

from dataclasses import dataclass

from .games import _safe_int

# --------------------------------------------------------------------- 稀有度

# 稀有度：(码, 名称, 徽章, 权值)。权值仅用于排序与占比展示，不做经济结算。
RARITIES: tuple[tuple[str, str, str, int], ...] = (
    ("common", "普通", "⚪", 1),
    ("rare", "稀有", "🔵", 2),
    ("epic", "史诗", "🟣", 3),
    ("legend", "传说", "🟡", 4),
)
RARITY_MAP: dict[str, tuple[str, str, int]] = {
    code: (name, badge, weight) for code, name, badge, weight in RARITIES
}
# 稀有度码 -> 权重，便于按稀有度排序
RARITY_WEIGHT: dict[str, int] = {code: weight for code, _n, _b, weight in RARITIES}

# --------------------------------------------------------------------- 分类

# 分类：(码, 名称, 徽章)
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("daily", "日常", "📅"),
    ("wealth", "财富", "💰"),
    ("game", "对局", "🎲"),
    ("social", "社交", "🤝"),
    ("luck", "运气", "🍀"),
    ("explore", "探索", "🧭"),
)
CATEGORY_MAP: dict[str, tuple[str, str]] = {
    code: (name, badge) for code, name, badge in CATEGORIES
}


def rarity_of(threshold: int, category: str) -> str:
    """由成就阈值与分类推导稀有度。

    刻意**不把稀有度写死在成就表里** —— 那样改阈值时文案会失真。
    这里按阈值分档，再对「探索」类整体上调一档（同样的数值，
    探索类更难达成，因为它要求多次完整对局）。

    Args:
        threshold: 成就阈值。
        category: 分类码。

    Returns:
        稀有度码（``common`` / ``rare`` / ``epic`` / ``legend``）。
    """
    value = max(0, _safe_int(threshold, 0))
    if value < 20:
        base = 0
    elif value < 100:
        base = 1
    elif value < 1000:
        base = 2
    else:
        base = 3
    if category == "explore":
        base = min(3, base + 1)
    elif category == "daily":
        base = max(0, base - 1)
    return RARITIES[base][0]


def rarity_badge(code: str) -> str:
    """稀有度码转徽章字符。

    Args:
        code: 稀有度码。

    Returns:
        徽章字符，未知码返回 ``❔``。
    """
    entry = RARITY_MAP.get(code or "")
    return entry[1] if entry else "❔"


def rarity_name(code: str) -> str:
    """稀有度码转中文名。

    Args:
        code: 稀有度码。

    Returns:
        中文名，未知码返回 ``未知``。
    """
    entry = RARITY_MAP.get(code or "")
    return entry[0] if entry else "未知"


def category_badge(code: str) -> str:
    """分类码转徽章字符。

    Args:
        code: 分类码。

    Returns:
        徽章字符，未知码返回 ``❔``。
    """
    entry = CATEGORY_MAP.get(code or "")
    return entry[1] if entry else "❔"


def category_name(code: str) -> str:
    """分类码转中文名。

    Args:
        code: 分类码。

    Returns:
        中文名，未知码返回 ``未分类``。
    """
    entry = CATEGORY_MAP.get(code or "")
    return entry[0] if entry else "未分类"


# 需要从检索词两端剥掉的字符：唤醒前缀 + 常见中英文标点。
# 之所以两端都要剥，是因为用户很容易写成「标签搜索 签到！」——
# 只在开头剥会得到「签到！」，与「签到」匹配不上（这是实际踩过的坑）。
_PUNCT = "/!#！＃／，,。.、；;：:？?＊*「」『』【】（）()[]<>《》\"' 	\u3000"


def normalize_query(text: str) -> str:
    """归一化检索词。

    去掉两端的唤醒前缀与常见标点、去掉全部空白、统一大小写，
    让 ``签到`` / ``/签到`` / ``签到！`` / `` 签 到 `` 都能命中同一条。

    Args:
        text: 原始检索词。

    Returns:
        归一化后的检索词。
    """
    cleaned = (text or "").strip(_PUNCT)
    return "".join(cleaned.split()).lower()


# --------------------------------------------------------------------- 标签条目


@dataclass(frozen=True)
class TagEntry:
    """一条标签（= 一个成就 / 收集项）的完整描述。

    Attributes:
        code: 唯一码，与 ``games_plus.ACHIEVEMENTS`` 的成就码一致。
        name: 展示名。
        category: 分类码。
        rarity: 稀有度码（由阈值推导）。
        threshold: 解锁阈值。
        reward: 解锁奖励积分。
        field_name: 计数的用户档案字段。
        current: 当前进度（已收敛到阈值）。
        unlocked: 是否已解锁。
        unlocked_at: 解锁时间戳（秒），0 表示未知/未解锁。
    """

    code: str
    name: str
    category: str
    rarity: str
    threshold: int
    reward: int
    field_name: str
    current: int
    unlocked: bool
    unlocked_at: int = 0

    @property
    def percent(self) -> int:
        """完成百分比（0~100）。"""
        if self.threshold <= 0:
            return 100
        return min(100, max(0, self.current * 100 // self.threshold))

    @property
    def remaining(self) -> int:
        """还差多少才算达成（已达成返回 0）。"""
        return max(0, self.threshold - self.current)

    def render(self) -> str:
        """渲染成一行展示文本。

        Returns:
            形如 ``🔵 ⚪ 签到七日 [日常/稀有] 5/7（还差 2）`` 的单行文本。
        """
        state = "✅" if self.unlocked else "🔒"
        badge = rarity_badge(self.rarity)
        head = f"{state}{badge} {self.name}"
        tail = f"[{category_name(self.category)}/{rarity_name(self.rarity)}]"
        if self.unlocked:
            return f"{head} {tail} 已达成（+{self.reward}）"
        return (
            f"{head} {tail} {self.current}/{self.threshold}"
            f"（还差 {self.remaining}，+{self.reward}）"
        )


def build_entries(
    achievements: list[tuple[str, str, str, int, int]],
    user: dict,
    *,
    categories: dict[str, str] | None = None,
    unlocked_at: dict[str, int] | None = None,
) -> list[TagEntry]:
    """把成就定义表与用户档案合成标签条目列表。

    Args:
        achievements: 形如 ``games_plus.ACHIEVEMENTS`` 的定义表：
            ``(码, 名称, 字段, 阈值, 奖励)``。
        user: 用户档案。
        categories: 成就码 -> 分类码的映射；缺省时全部归入 ``game``。
        unlocked_at: 成就码 -> 解锁时间戳，用于「按获得时间」排序。

    Returns:
        标签条目列表，顺序与传入定义表一致。
    """
    categories = categories or {}
    unlocked_at = unlocked_at or {}
    owned = set(user.get("achievements") or [])
    entries: list[TagEntry] = []
    for code, name, field_name, threshold, reward in achievements:
        category = categories.get(code, "game")
        current = max(0, _safe_int(user.get(field_name, 0), 0))
        entries.append(
            TagEntry(
                code=code,
                name=name,
                category=category,
                rarity=rarity_of(threshold, category),
                threshold=max(0, threshold),
                reward=max(0, reward),
                field_name=field_name,
                current=min(current, max(0, threshold)),
                unlocked=code in owned,
                unlocked_at=max(0, _safe_int(unlocked_at.get(code, 0), 0)),
            )
        )
    return entries


def filter_entries(
    entries: list[TagEntry],
    *,
    query: str = "",
    category: str = "",
    rarity: str = "",
    only_locked: bool = False,
    only_unlocked: bool = False,
) -> list[TagEntry]:
    """按关键词 / 分类 / 稀有度筛选标签。

    关键词会同时匹配成就名、成就码与分类名，并且先做归一化，
    因此中英文大小写、空白、标点都不会影响命中。

    Args:
        entries: 标签条目。
        query: 关键词（可为空）。
        category: 分类码或分类名（可为空）。
        rarity: 稀有度码或稀有度名（可为空）。
        only_locked: 只看未解锁。
        only_unlocked: 只看已解锁。

    Returns:
        筛选后的列表。
    """
    key = normalize_query(query)
    cat = normalize_query(category)
    rar = normalize_query(rarity)
    cat_code = resolve_category(cat)
    rar_code = resolve_rarity(rar)

    result: list[TagEntry] = []
    for entry in entries:
        if cat and entry.category != cat_code:
            continue
        if rar and entry.rarity != rar_code:
            continue
        if only_locked and entry.unlocked:
            continue
        if only_unlocked and not entry.unlocked:
            continue
        if key:
            haystack = (
                normalize_query(entry.name)
                + normalize_query(entry.code)
                + normalize_query(category_name(entry.category))
                + normalize_query(rarity_name(entry.rarity))
            )
            if key not in haystack:
                continue
        result.append(entry)
    return result


def resolve_category(text: str) -> str:
    """把分类名或分类码统一成分类码。

    Args:
        text: 已归一化的输入。

    Returns:
        分类码，无法识别时原样返回。
    """
    if not text:
        return ""
    for code, name, _badge in CATEGORIES:
        if text in (normalize_query(code), normalize_query(name)):
            return code
    return text


def resolve_rarity(text: str) -> str:
    """把稀有度名或码统一成稀有度码。

    Args:
        text: 已归一化的输入。

    Returns:
        稀有度码，无法识别时原样返回。
    """
    if not text:
        return ""
    for code, name, _badge, _weight in RARITIES:
        if text in (normalize_query(code), normalize_query(name)):
            return code
    return text


def sort_entries(
    entries: list[TagEntry],
    *,
    by: str = "",
    descending: bool = True,
) -> list[TagEntry]:
    """排序标签条目。

    Args:
        entries: 标签条目。
        by: 排序维度：``rarity`` / ``progress`` / ``name`` / ``time``（空为默认）。
            ``time`` 按解锁时间倒序，未解锁的排在最后。
        descending: 是否降序。

    Returns:
        新列表（不修改入参）。
    """
    key = normalize_query(by)
    if key in ("rarity", "稀有度"):
        return sorted(
            entries,
            key=lambda e: (RARITY_WEIGHT.get(e.rarity, 0), e.current),
            reverse=descending,
        )
    if key in ("progress", "进度"):
        return sorted(
            entries,
            key=lambda e: (e.unlocked, e.percent, e.current),
            reverse=descending,
        )
    if key in ("name", "名称"):
        return sorted(entries, key=lambda e: e.name, reverse=descending)
    if key in ("time", "时间"):
        # 未解锁（unlocked_at == 0）恒排最后，不受 descending 影响
        return sorted(
            entries,
            key=lambda e: (
                e.unlocked_at > 0,
                e.unlocked_at if descending else -e.unlocked_at,
            ),
            reverse=True,
        )
    return list(entries)


def summarize(entries: list[TagEntry]) -> dict[str, object]:
    """汇总标签统计信息。

    Args:
        entries: 标签条目。

    Returns:
        含 ``total`` / ``unlocked`` / ``percent`` / ``by_rarity`` /
        ``by_category`` 的字典。
    """
    total = len(entries)
    unlocked = sum(1 for e in entries if e.unlocked)
    by_rarity: dict[str, list[int]] = {code: [0, 0] for code, *_ in RARITIES}
    by_category: dict[str, list[int]] = {code: [0, 0] for code, *_ in CATEGORIES}
    for entry in entries:
        rar = by_rarity.setdefault(entry.rarity, [0, 0])
        rar[0] += 1
        rar[1] += 1 if entry.unlocked else 0
        cat = by_category.setdefault(entry.category, [0, 0])
        cat[0] += 1
        cat[1] += 1 if entry.unlocked else 0
    return {
        "total": total,
        "unlocked": unlocked,
        "percent": (unlocked * 100 // total) if total else 0,
        "by_rarity": by_rarity,
        "by_category": by_category,
    }


def render_summary(entries: list[TagEntry]) -> str:
    """渲染统计摘要（多行）。

    Args:
        entries: 标签条目。

    Returns:
        形如 ``已收集 5/22（22%）`` 加分类明细的文本。
    """
    info = summarize(entries)
    lines = [f"🏷️ 标签收集：{info['unlocked']}/{info['total']}（{info['percent']}%）"]
    for code, _name, badge in CATEGORIES:
        total, got = info["by_category"].get(code, [0, 0])
        if total:
            lines.append(f"  {badge} {category_name(code)} {got}/{total}")
    for code, name, badge, _weight in RARITIES:
        total, got = info["by_rarity"].get(code, [0, 0])
        if total:
            lines.append(f"  {badge} {name} {got}/{total}")
    return "\n".join(lines)


def render_list(entries: list[TagEntry], limit: int = 20) -> str:
    """渲染标签列表。

    Args:
        entries: 标签条目。
        limit: 最多展示多少条。

    Returns:
        多行文本；空列表给出明确提示而不是空串。
    """
    if not entries:
        return "没有匹配的标签，换个关键词试试（例如：标签 稀有 / 标签 日常）。"
    shown = entries[: max(1, limit)]
    lines = [e.render() for e in shown]
    if len(entries) > len(shown):
        lines.append(f"…… 还有 {len(entries) - len(shown)} 条，可用筛选条件收窄。")
    return "\n".join(lines)


def is_category(text: str) -> bool:
    """判断文本是否是已知分类（名或码）。

    Args:
        text: 原始文本。

    Returns:
        是否可识别为分类。
    """
    norm = normalize_query(text)
    if not norm:
        return False
    return any(norm in (code, normalize_query(name)) for code, name, _b in CATEGORIES)


def is_rarity(text: str) -> bool:
    """判断文本是否是已知稀有度（名或码）。

    Args:
        text: 原始文本。

    Returns:
        是否可识别为稀有度。
    """
    norm = normalize_query(text)
    if not norm:
        return False
    return any(norm in (code, normalize_query(name)) for code, name, _b, _w in RARITIES)

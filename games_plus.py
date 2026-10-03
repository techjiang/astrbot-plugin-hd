"""互动玩法的第二批扩展：对局类与成长类玩法的纯逻辑层。

与 ``games.py`` / ``games_extra.py`` 保持同一套设计约束：

- **无 IO、无全局可变状态**，随机性一律通过 ``rng`` 注入，便于测试复现；
- 涉及积分的函数保证**账目守恒**（返回值与余额变化严格一致）；
- 数值边界先收敛再运算，脏配置不会把插件炸掉；
- 展示文案集中成常量池，避免散落在主逻辑里。

覆盖：弹幕竞猜、井字棋、扫雷、Codebreaker、抛硬币、决斗盘（RPSLS）、
幸运大转盘、成就系统、每日商店折扣、转生与全局加成。
"""

from __future__ import annotations

import random
import time
from collections import Counter
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


def _normalize_option(text: str) -> str:
    """归一化文本，用于「猜选项」这类匹配。

    去掉首尾空白、剥掉唤醒前缀与常见标点，并统一大小写。

    Args:
        text: 原始文本。

    Returns:
        归一化后的文本。
    """
    cleaned = (text or "").strip().lstrip("/!#！＃／").strip()
    return "".join(cleaned.split()).lower()


# --------------------------------------------------------------------- 弹幕竞猜

# 赔率下限（低于 1 表示连本金都拿不回，观感极差）与上限。
WAGER_MIN_ODDS = 1.05
WAGER_MAX_ODDS = 20.0


def wager_payout(
    bet: int,
    odds: float,
    winners: int = 1,
    losers_pool: int = 0,
    winner_pool: int = 0,
) -> tuple[int, int]:
    """按「零和」口径结算一次竞猜。

    这是本模块最重要的不变量：**所有赔付只能出自输家押注的本金**。
    直觉上「赔率 = 总池 ÷ 该选项池」很自然，但它有一个致命缺陷 ——
    当**所有人都押同一个选项**时，中奖者拿回的是自己的钱，
    却按赔率放大，凭空多出来的部分就是**新印的积分**。

    复现（三个玩家全押 B）：池子 337 全是自己的，赔率 1.9，
    派彩 337 × 1.9 = 640，净增 303 分。

    所以这里改成显式的零和分配：

    - 中奖者之间**按注额比例**瓜分「输家池」；
    - 中奖者各自的本金**原样退回**（不赚不亏，也不会亏）；
    - 无人中奖时，池子按调用方的规则处理（默认留在系统里，即通缩）。

    这样无论下注分布如何，``sum(净收益) == -losers_pool <= 0`` 恒成立。

    Args:
        bet: 本次下注额。
        odds: 展示用赔率（仅用于回报文案，不影响分配）。
        winners: 该选项的中奖人数。
        losers_pool: **输家**押注的总额（可分配奖金池）。
        winner_pool: **中奖者**押注的总额（用于按比例分配）。

    Returns:
        ``(总返还, 净收益)``，总返还包括本金。
    """
    bet = max(0, _safe_int(bet))
    losers_pool = max(0, _safe_int(losers_pool))
    winner_pool = max(0, _safe_int(winner_pool))
    if winner_pool <= 0:
        return bet, 0
    # 按注额比例分奖金；整数除法会在末位丢分，丢的那点留在系统里（偏通缩，安全）
    share = bet * losers_pool // winner_pool
    total = bet + share
    return total, share


def wager_odds(option_pool: dict[str, int], options: list[str]) -> dict[str, float]:
    """推算各选项的展示赔率（含本金）。

    口径：**能拿回多少，取决于别人押了多少**。

    - 有人押中你选的选项时，赔率 = ``(你的注 + 输家池中你那份) / 你的注``；
      极端情况下只有你一个人押中，可以独吞整个输家池；
    - 所有人都押同一选项时，没有输家，赔率退化为 **1.0**（只退本金）——
      这正是零和约束下的正确答案，也是防止刷分的关键；
    - 无人下注的选项按上限展示，鼓励接冷门。

    Args:
        option_pool: 选项 -> 该选项下注总额。
        options: 全部选项。

    Returns:
        选项 -> 赔率（保留两位小数）。
    """
    total = sum(max(0, int(v)) for v in option_pool.values())
    odds: dict[str, float] = {}
    for opt in options:
        pool = max(0, int(option_pool.get(opt, 0)))
        losses = total - pool
        if pool <= 0:
            odds[opt] = WAGER_MAX_ODDS
            continue
        if losses <= 0:
            # 所有人都押了这一个选项：没有输家，只能退本金
            odds[opt] = 1.0
            continue
        value = (pool + losses) / pool
        odds[opt] = round(min(WAGER_MAX_ODDS, max(WAGER_MIN_ODDS, value)), 2)
    return odds


@dataclass
class WagerGame:
    """弹幕竞猜（预测）状态。

    Attributes:
        question: 竞猜题目。
        options: 选项列表。
        owner: 发起人。
        bets: ``uid -> (选项序号, 下注额)``，一人一注、可覆盖。
        option_pool: 选项 -> 下注总额。
        started_at: 开始时间戳。
        duration: 有效时长（秒）。
        settled: 是否已结算。
        answer: 开奖后的正确选项序号。
    """

    question: str
    options: list[str]
    owner: str = ""
    bets: dict[str, tuple[int, int]] = field(default_factory=dict)
    option_pool: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    duration: int = 300
    settled: bool = False
    answer: int = -1

    @classmethod
    def new(
        cls,
        question: str,
        options: list[str],
        owner: str = "",
        duration: int = 300,
    ) -> WagerGame:
        """创建一盘竞猜。

        Args:
            question: 题目。
            options: 选项（至少 2 个，超出会被截断到 8 个）。
            owner: 发起人。
            duration: 时长（秒），收敛到 30 ~ 3600。

        Returns:
            初始化后的竞猜。
        """
        opts = [str(o).strip() for o in options if str(o).strip()][:8]
        return cls(
            question=str(question).strip(),
            options=opts,
            owner=str(owner),
            duration=_clamp_int(duration, 30, 3600, 300),
        )

    def expired(self, now: float | None = None) -> bool:
        """是否已到开奖时间。

        Args:
            now: 当前时间戳，默认取系统时间。

        Returns:
            是否过期。
        """
        now = time.time() if now is None else now
        return now - self.started_at >= self.duration

    def odds(self) -> dict[str, float]:
        """当前赔率表（零和口径，内部按实时池子推算）。"""
        return wager_odds(self.option_pool, self.options)

    def total_pool(self) -> int:
        """当前总池。"""
        return sum(self.option_pool.values())

    def place(self, uid: str, index: int, amount: int) -> tuple[bool, str]:
        """下一注（同一人重复下注会覆盖旧注，差额由调用方结算）。

        Args:
            uid: 下注人。
            index: 选项序号（从 1 开始）。
            amount: 下注额。

        Returns:
            ``(是否成功, 提示)``；提示为 ``"delta:<n>"`` 时表示调用方
            需要按 ``n`` 调整余额（正数退回，负数扣款）。
        """
        uid = str(uid)
        amount = max(0, _safe_int(amount))
        if self.settled:
            return False, "这一局已经开奖啦。"
        if self.expired():
            return False, "已经到开奖时间，下注关闭。"
        if not 1 <= index <= len(self.options):
            return False, f"选项序号应为 1 ~ {len(self.options)}。"
        if amount <= 0:
            return False, "下注额必须是不小于 1 的整数。"
        opt = self.options[index - 1]
        old_index, old_amount = self.bets.get(uid, (0, 0))
        # 关键：无论同选项加注还是换选项，旧注都必须先从它原本所在的
        # 选项池里全额扣掉，否则总池会随每次加注虚增，赔率随之失真。
        if old_index:
            old_opt = self.options[old_index - 1]
            self.option_pool[old_opt] = max(
                0, self.option_pool.get(old_opt, 0) - old_amount
            )
        delta = old_amount - amount  # 正数表示该退回给玩家，负数表示需补扣
        self.option_pool[opt] = self.option_pool.get(opt, 0) + amount
        # 清理掉 0 池，避免占位选项在赔率表里显示成「有人下注」
        self.option_pool = {k: v for k, v in self.option_pool.items() if v > 0}
        self.bets[uid] = (index, amount)
        return True, f"delta:{delta}"

    def settle(self, answer: int, rng: random.Random | None = None) -> int:
        """开奖并返回正确选项序号。

        Args:
            answer: 正确选项序号；``0`` 表示随机抽出。
            rng: 随机源，``answer`` 为 0 时使用。

        Returns:
            正确选项序号（1 起）。
        """
        rng = rng or random
        self.settled = True
        if answer in range(1, len(self.options) + 1):
            self.answer = answer
        else:
            self.answer = rng.randint(1, len(self.options))
        return self.answer


# --------------------------------------------------------------------- 井字棋

TICTACTOE_LINES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


def tictactoe_winner(board: list[str]) -> str:
    """判断井字棋棋盘胜负。

    Args:
        board: 长度 9 的棋盘，空格用 ``""``。

    Returns:
        获胜方 ``"X"`` / ``"O"``；未分胜负返回空字符串。
    """
    for a, b, c in TICTACTOE_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return ""


def tictactoe_ai_move(
    board: list[str], me: str, rng: random.Random | None = None
) -> int:
    """为 AI 选出一步棋，优先级：必胜 > 必守 > 中心 > 角 > 随机。

    判定胜负时**必须看「整条连线里已有两个我的子 + 一个空格」**，
    而不能用「落子后是否成三」再逐个空格试探 —— 后者会被对手已经占住
    的格子干扰，而且漏掉「堵对手」这一步时，一个只会
    「中心 > 角 > 随机」的 AI 会频繁输给乱下的对手。

    Args:
        board: 当前棋盘。
        me: AI 的棋子（``"X"`` / ``"O"``）。
        rng: 随机源。

    Returns:
        落子格号（0~8）；棋盘已满时返回 ``-1``。
    """
    rng = rng or random
    foe = "O" if me == "X" else "X"
    empty = [i for i, ch in enumerate(board) if not ch]
    if not empty:
        return -1

    def winning_cell(mark: str) -> int:
        """找一条「已有两个 mark + 一个空格」的连线，返回那个空格。"""
        for a, b, c in TICTACTOE_LINES:
            line = (board[a], board[b], board[c])
            if line.count(mark) == 2 and line.count("") == 1:
                return [a, b, c][line.index("")]
        return -1

    move = winning_cell(me)  # 自己能赢就赢
    if move >= 0:
        return move
    move = winning_cell(foe)  # 对手要赢就堵
    if move >= 0:
        return move
    if not board[4]:
        return 4
    corners = [i for i in (0, 2, 6, 8) if not board[i]]
    if corners:
        return rng.choice(corners)
    return rng.choice(empty)


def tictactoe_render(board: list[str], moves: dict[str, int] | None = None) -> str:
    """把棋盘渲染成三行文本。

    Args:
        board: 长度 9 的棋盘。
        moves: 格号 -> 玩家标记，用于标注归属（可选）。

    Returns:
        三行棋盘文本。
    """
    marks = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣"}
    del marks  # 保留常量便于将来复用；当前统一用纯符号更省字符
    moves = moves or {}
    rows = []
    for r in range(3):
        cells = []
        for c in range(3):
            i = r * 3 + c
            ch = board[i] or "·"
            tag = moves.get(str(i), "")
            cells.append(f"{ch}{tag}" if tag else ch)
        rows.append(" ".join(cells))
    return "\n".join(rows)


@dataclass
class TicTacToeGame:
    """人机井字棋对局。

    Attributes:
        board: 长度 9 的棋盘。
        human: 人类棋子。
        ai: AI 棋子。
        turn: 当前轮到谁。
        started_at: 开始时间戳。
        finished: 是否已结束。
    """

    board: list[str] = field(default_factory=lambda: [""] * 9)
    human: str = "X"
    ai: str = "O"
    turn: str = "X"
    started_at: float = field(default_factory=time.time)
    finished: bool = False

    @classmethod
    def new(cls, human: str = "X") -> TicTacToeGame:
        """开一局（人类先手）。

        Args:
            human: 人类棋子，非 ``"O"`` 时按 ``"X"`` 处理。

        Returns:
            初始化后的对局。
        """
        man = "O" if str(human).upper() == "O" else "X"
        return cls(human=man, ai="O" if man == "X" else "X", turn=man)

    def play(self, cell: int, rng: random.Random | None = None) -> tuple[str, int]:
        """人类落子，随后 AI 立刻应手。

        Args:
            cell: 人类落子格号（0~8）。
            rng: AI 随机源。

        Returns:
            ``(结果, AI落子格号)``，结果为
            ``invalid``/``win``/``lose``/``draw``/``continue``。
        """
        if self.finished:
            return "finished", -1
        if not 0 <= cell <= 8 or self.board[cell]:
            return "invalid", -1
        self.board[cell] = self.human
        if tictactoe_winner(self.board) == self.human:
            self.finished = True
            return "win", -1
        if all(self.board):
            self.finished = True
            return "draw", -1
        move = tictactoe_ai_move(self.board, self.ai, rng)
        if move < 0:
            self.finished = True
            return "draw", -1
        self.board[move] = self.ai
        if tictactoe_winner(self.board) == self.ai:
            self.finished = True
            return "lose", move
        if all(self.board):
            self.finished = True
            return "draw", move
        return "continue", move

    def render(self) -> str:
        """渲染棋盘（带格号提示，空格显示为序号）。"""
        lines = []
        for r in range(3):
            cells = []
            for c in range(3):
                i = r * 3 + c
                cells.append(self.board[i] or str(i + 1))
            lines.append(" │ ".join(cells))
        return "\n".join(lines)


# --------------------------------------------------------------------- 扫雷


@dataclass
class MinefieldGame:
    """多人共建扫雷盘（踩雷即结束）。

    Attributes:
        size: 棋盘边长。
        mines: 雷数。
        cells: 每格的提示值：``-1`` 为雷，``0~8`` 为邻雷数。
        opened: 已翻开格号。
        started_at: 开始时间戳。
        flags: 已被标记的格号（仅展示用）。
    """

    size: int
    mines: int
    cells: list[int]
    opened: set[int] = field(default_factory=set)
    flags: set[int] = field(default_factory=set)
    started_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        size: int = 6,
        mines: int = 6,
        rng: random.Random | None = None,
        first_safe: int = -1,
    ) -> MinefieldGame:
        """生成一张扫雷盘。

        Args:
            size: 边长，收敛到 3 ~ 8。
            mines: 雷数，收敛到 1 ~ ``size*size-1``。
            rng: 随机源。
            first_safe: 保证该格不是雷（先手保护），负数表示不保护。

        Returns:
            生成好的扫雷盘。
        """
        rng = rng or random
        n = _clamp_int(size, 3, 8, 6)
        total = n * n
        m = _clamp_int(mines, 1, total - 1, max(1, total // 6))
        pool = [i for i in range(total) if i != first_safe]
        picked = set(rng.sample(pool, min(m, len(pool))))
        cells = []
        for i in range(total):
            if i in picked:
                cells.append(-1)
                continue
            r, c = divmod(i, n)
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < n and 0 <= nc < n and (nr * n + nc) in picked:
                        count += 1
            cells.append(count)
        return cls(size=n, mines=len(picked), cells=cells)

    def open(self, index: int) -> tuple[bool, str, set[int]]:
        """翻开一格。

        Args:
            index: 格号（0 起）。

        Returns:
            ``(是否踩雷, 提示, 本次连带翻开的格号集合)``。
        """
        total = self.size * self.size
        if not 0 <= index < total:
            return False, f"格号要在 1 ~ {total} 之间。", set()
        if index in self.opened:
            return False, "这一格已经翻开了。", set()
        if self.cells[index] == -1:
            self.opened.add(index)
            return True, "💥 踩到雷了！", {index}

        opened: set[int] = set()
        stack = [index]
        while stack:
            cur = stack.pop()
            if cur in self.opened or cur in opened:
                continue
            opened.add(cur)
            if self.cells[cur] != 0:
                continue
            r, c = divmod(cur, self.size)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < self.size and 0 <= nc < self.size:
                        nxt = nr * self.size + nc
                        if nxt not in opened and self.cells[nxt] != -1:
                            stack.append(nxt)
        self.opened |= opened
        left = sum(
            1 for i in range(total) if i not in self.opened and self.cells[i] != -1
        )
        if left == 0:
            return False, "🎉 全部安全格都翻开了，扫雷成功！", opened
        return False, f"安全，翻开 {len(opened)} 格，剩余 {left} 格。", opened

    def cleared(self) -> bool:
        """是否已清完所有安全格。"""
        total = self.size * self.size
        return all(i in self.opened or self.cells[i] == -1 for i in range(total))

    def render(self) -> str:
        """渲染棋盘（未翻开显示序号，已翻开显示数字或雷）。"""
        rows = []
        for r in range(self.size):
            cells = []
            for c in range(self.size):
                i = r * self.size + c
                if i in self.opened:
                    v = self.cells[i]
                    cells.append("💥" if v == -1 else (str(v) if v else "🟦"))
                else:
                    cells.append(f"{i + 1:>2}")
            rows.append(" ".join(cells))
        return "\n".join(rows)


# --------------------------------------------------------------------- Codebreaker

# 数字猜解默认位数与允许重复
CODEBREAKER_DIGITS = 4


def codebreaker_guess(secret: str, guess: str) -> tuple[int, int]:
    """按 Mastermind 规则比对一次猜测。

    Args:
        secret: 谜底数字串。
        guess: 猜测数字串（长度应与谜底一致）。

    Returns:
        ``(数字与位置都对的数量, 数字对但位置错的数量)``。
    """
    secret = str(secret)
    guess = str(guess)
    exact = sum(1 for a, b in zip(secret, guess, strict=False) if a == b)
    sec = Counter(secret)
    gue = Counter(guess)
    common = sum(min(sec[d], gue[d]) for d in sec)
    return exact, max(0, common - exact)


@dataclass
class CodebreakerGame:
    """数字猜解对局。

    Attributes:
        secret: 谜底。
        digits: 位数。
        max_attempts: 最大次数。
        history: ``[(猜测, 位置对, 数字对), ...]``。
        started_at: 开始时间戳。
    """

    secret: str
    digits: int = CODEBREAKER_DIGITS
    max_attempts: int = 8
    history: list[tuple[str, int, int]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        digits: int = CODEBREAKER_DIGITS,
        max_attempts: int = 8,
        rng: random.Random | None = None,
    ) -> CodebreakerGame:
        """生成一个数字谜底。

        Args:
            digits: 位数，收敛到 3 ~ 6。
            max_attempts: 最大次数，收敛到 3 ~ 20。
            rng: 随机源。

        Returns:
            初始化后的对局。
        """
        rng = rng or random
        n = _clamp_int(digits, 3, 6, CODEBREAKER_DIGITS)
        first = rng.choice("123456789")
        rest = "".join(rng.choice("0123456789") for _ in range(n - 1))
        return cls(
            secret=first + rest,
            digits=n,
            max_attempts=_clamp_int(max_attempts, 3, 20, 8),
        )

    @property
    def attempts(self) -> int:
        """已用次数。"""
        return len(self.history)

    def submit(self, guess: str) -> tuple[str, str, int, int]:
        """提交一次猜测。

        Args:
            guess: 猜测的数字串。

        Returns:
            ``(结果, 提示, 位置对, 数字对)``，结果为
            ``invalid``/``win``/``lose``/``continue``。
        """
        g = "".join(ch for ch in str(guess) if ch.isdigit())
        if len(g) != self.digits:
            return "invalid", f"请发 {self.digits} 位数字。", 0, 0
        exact, misplaced = codebreaker_guess(self.secret, g)
        self.history.append((g, exact, misplaced))
        if exact == self.digits:
            return "win", f"🎉 破解成功！答案就是 {g}。", exact, misplaced
        if self.attempts >= self.max_attempts:
            return "lose", f"次数用完了，答案是 {self.secret}。", exact, misplaced
        return (
            "continue",
            f"{exact} 个数字正确且位置正确，{misplaced} 个数字正确但位置不对。",
            exact,
            misplaced,
        )


# --------------------------------------------------------------------- 抛硬币

COIN_SIDES: tuple[str, ...] = ("正面", "反面")
COIN_ALIASES: dict[str, str] = {
    "正": "正面",
    "正 面": "正面",
    "正面": "正面",
    "head": "正面",
    "heads": "正面",
    "h": "正面",
    "阳": "正面",
    "反": "反面",
    "反面": "反面",
    "tail": "反面",
    "tails": "反面",
    "t": "反面",
    "阴": "反面",
}


def resolve_coin_bet(text: str) -> str:
    """把用户输入解析成硬币面。

    Args:
        text: 用户输入，如 ``正`` / ``heads``。

    Returns:
        ``"正面"`` / ``"反面"``；无法识别时返回空字符串。
    """
    key = _normalize_option(text)
    return COIN_ALIASES.get(key, "")


def flip_coin(rng: random.Random | None = None) -> str:
    """抛一枚硬币。

    Args:
        rng: 随机源。

    Returns:
        ``"正面"`` 或 ``"反面"``。
    """
    rng = rng or random
    return rng.choice(COIN_SIDES)


# --------------------------------------------------------------------- 决斗盘

# 石头剪刀布蜥蜴斯波克：每个手势克制另外两个。
RPSLS_BEATS: dict[str, tuple[str, ...]] = {
    "石头": ("剪刀", "蜥蜴"),
    "剪刀": ("布", "蜥蜴"),
    "布": ("石头", "斯波克"),
    "蜥蜴": ("布", "斯波克"),
    "斯波克": ("石头", "剪刀"),
}
RPSLS_ALIASES: dict[str, str] = {
    "石头": "石头",
    "石": "石头",
    "rock": "石头",
    "r": "石头",
    "剪刀": "剪刀",
    "剪": "剪刀",
    "scissors": "剪刀",
    "s": "剪刀",
    "布": "布",
    "paper": "布",
    "p": "布",
    "蜥蜴": "蜥蜴",
    "lizard": "蜥蜴",
    "l": "蜥蜴",
    "斯波克": "斯波克",
    "史波克": "斯波克",
    "spock": "斯波克",
    "k": "斯波克",
}


def resolve_rpsls(text: str) -> str:
    """把用户输入解析成标准手势名。

    Args:
        text: 用户输入。

    Returns:
        标准手势名；无法识别时返回空字符串。
    """
    return RPSLS_ALIASES.get(_normalize_option(text), "")


def rpsls_judge(a: str, b: str) -> str:
    """判定两个手势的胜负。

    Args:
        a: 甲方手势。
        b: 乙方手势。

    Returns:
        ``"a"`` / ``"b"`` / ``"draw"``。
    """
    if a not in RPSLS_BEATS or b not in RPSLS_BEATS:
        return "draw"
    if a == b:
        return "draw"
    return "a" if b in RPSLS_BEATS[a] else "b"


def rpsls_flavor(a: str, b: str) -> str:
    """生成一句判定说明（如「剪刀 剪断 布」）。

    Args:
        a: 胜方手势。
        b: 负方手势。

    Returns:
        说明文案。
    """
    verbs = {
        ("石头", "剪刀"): "砸坏",
        ("石头", "蜥蜴"): "压扁",
        ("剪刀", "布"): "剪断",
        ("剪刀", "蜥蜴"): "斩首",
        ("布", "石头"): "包住",
        ("布", "斯波克"): "推翻",
        ("蜥蜴", "布"): "吃掉",
        ("蜥蜴", "斯波克"): "毒倒",
        ("斯波克", "石头"): "蒸发",
        ("斯波克", "剪刀"): "钝化",
    }
    return f"{a} {verbs.get((a, b), '击败')} {b}"


# --------------------------------------------------------------------- 幸运大转盘

# 转盘扇区：(权重, 类型, 数值, 展示文本)；amount=积分，item=称号。
#
# 数值经过收敛：单次消耗 50 时，纯积分的期望回报约 41.9（回收率 0.84），
# 加上 4% 概率的限定称号，整体期望仍**严格小于消耗** —— 不存在刷分空间。
# 这是本模块最重要的不变量，改动奖池必须同步看 tests 里的期望断言。
WHEEL_SECTORS: list[tuple[int, str, int, str]] = [
    (26, "amount", 0, "空手而归"),
    (22, "amount", 10, "小奖 10"),
    (18, "amount", 30, "中奖 30"),
    (14, "amount", 60, "大奖 60"),
    (8, "amount", 120, "超级大奖 120"),
    (6, "amount", 200, "头奖 200"),
    (4, "item", 0, "称号：转盘之王"),
    (2, "amount", 400, "欧皇时刻 400"),
]

# 转盘单次消耗的**参考值**，仅用于测试与文档里的期望核算。
# 运行期以配置 ``wheel.cost`` 为准。
WHEEL_BASE_COST = 50


def wheel_spin(cost: int, rng: random.Random | None = None) -> tuple[str, int, str]:
    """转动一次大转盘。

    Args:
        cost: 单次消耗（并入返回值，便于调用方直接结算）。
        rng: 随机源。

    Returns:
        ``(类型, 积分净变动, 展示文本)``，类型为 ``amount`` / ``item``。
    """
    rng = rng or random
    weights = [w for w, *_ in WHEEL_SECTORS]
    _, kind, value, label = rng.choices(WHEEL_SECTORS, weights=weights, k=1)[0]
    if kind == "amount":
        return "amount", int(value) - max(0, _safe_int(cost)), label
    return "item", -max(0, _safe_int(cost)), label


# --------------------------------------------------------------------- 成就系统

# 成就定义：(成就码, 名称, 条件字段, 阈值, 奖励积分)
ACHIEVEMENTS: list[tuple[str, str, str, int, int]] = [
    ("checkin_7", "签到七日", "best_streak", 7, 60),
    ("checkin_30", "签到满月", "best_streak", 30, 300),
    ("rich_1k", "小康之家", "balance", 1000, 50),
    ("rich_1w", "万元户", "balance", 10000, 400),
    ("rich_10w", "富甲一方", "balance", 100000, 2000),
    ("lottery_50", "抽奖常客", "lottery_count", 50, 80),
    ("lottery_500", "抽奖狂魔", "lottery_count", 500, 600),
    ("guess_30", "神算子", "guess_win", 30, 120),
    ("chain_50", "接龙达人", "chain_win", 50, 150),
    ("dice_100", "骰子信徒", "dice_count", 100, 100),
    ("rob_20", "江洋大盗", "rob_win", 20, 200),
    ("bj_30", "赌神", "bj_win", 30, 150),
    ("duel_30", "武斗之王", "duel_win", 30, 150),
    ("riddle_20", "博闻强识", "riddle_win", 20, 120),
    ("soup_10", "推理爱好者", "soup_open", 10, 80),
    ("ttt_10", "棋逢对手", "ttt_win", 10, 100),
    ("mine_10", "扫雷工兵", "mine_clear", 10, 100),
    ("code_5", "破译者", "code_win", 5, 200),
    ("coin_5", "硬币之王", "coin_win", 5, 80),
    ("wager_5", "预言家", "wager_win", 5, 120),
    ("gift_50", "送礼达人", "gift_sent", 50, 120),
    ("confess_10", "情话选手", "confess_count", 10, 60),
]
ACHIEVEMENT_MAP: dict[str, tuple[str, str, int, int]] = {
    code: (name, field_name, threshold, reward)
    for code, name, field_name, threshold, reward in ACHIEVEMENTS
}

# 成就码 -> 标签分类码。分类码定义见 ``games_tags.CATEGORIES``。
#
# 单独放一张表而不是塞进 ``ACHIEVEMENTS`` 元组，是为了不破坏已有的
# 4 元组解包（主逻辑与测试里有大量 ``for _code, name, cur, th, ok in ...``）。
# 新增成就若忘记登记，``games_tags.build_entries`` 会归入 ``game`` 默认分类，
# ``tests/test_tags.py`` 里有断言保证两者严格对齐。
ACHIEVEMENT_CATEGORIES: dict[str, str] = {
    "checkin_7": "daily",
    "checkin_30": "daily",
    "rich_1k": "wealth",
    "rich_1w": "wealth",
    "rich_10w": "wealth",
    "lottery_50": "luck",
    "lottery_500": "luck",
    "guess_30": "game",
    "chain_50": "game",
    "dice_100": "luck",
    "rob_20": "social",
    "bj_30": "game",
    "duel_30": "social",
    "riddle_20": "explore",
    "soup_10": "explore",
    "ttt_10": "game",
    "mine_10": "game",
    "code_5": "explore",
    "coin_5": "luck",
    "wager_5": "luck",
    "gift_50": "social",
    "confess_10": "social",
}


def achievement_progress(user: dict) -> list[tuple[str, str, int, int, bool]]:
    """计算全部成就的进度。

    Args:
        user: 用户档案。

    Returns:
        ``[(成就码, 名称, 当前值, 阈值, 是否已解锁), ...]``。
    """
    unlocked = set(user.get("achievements") or [])
    rows: list[tuple[str, str, int, int, bool]] = []
    for code, name, field_name, threshold, _reward in ACHIEVEMENTS:
        current = max(0, _safe_int(user.get(field_name, 0), 0))
        rows.append((code, name, min(current, threshold), threshold, code in unlocked))
    return rows


def check_achievements(
    user: dict, newly: list[str] | None = None
) -> tuple[list[tuple[str, str, int]], int]:
    """检查并解锁新成就。

    Args:
        user: 用户档案（就地更新 ``achievements``）。
        newly: 追加解锁的成就码（可选，便于外部先注入）。

    Returns:
        ``([(成就码, 名称, 奖励积分), ...], 奖励合计)``。
    """
    if newly:
        owned = set(user.get("achievements") or [])
        owned.update(newly)
        user["achievements"] = sorted(owned)

    owned = set(user.get("achievements") or [])
    gained: list[tuple[str, str, int]] = []
    total = 0
    now = int(time.time())
    stamps = user.get("achievement_ts")
    if not isinstance(stamps, dict):
        stamps = {}
        user["achievement_ts"] = stamps
    for code, name, field_name, threshold, reward in ACHIEVEMENTS:
        if code in owned:
            continue
        if max(0, _safe_int(user.get(field_name, 0), 0)) >= threshold:
            owned.add(code)
            gained.append((code, name, reward))
            total += reward
            stamps.setdefault(code, now)
    if gained:
        user["achievements"] = sorted(owned)
        user["balance"] = _safe_int(user.get("balance", 0), 0) + total
    return gained, total


def achievement_unlocked_at(user: dict) -> dict[str, int]:
    """读取成就的解锁时间戳表。

    ``achievement_ts`` 是 v1.3.0 才引入的字段，老数据里没有；此时返回空表，
    调用方（``games_tags``）会把时间视为未知，而不是伪造一个 ``0`` 时间。

    Args:
        user: 用户档案。

    Returns:
        成就码 -> unix 时间戳（秒）。非法的值会被剔除。
    """
    raw = user.get("achievement_ts")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for code, value in raw.items():
        if not isinstance(code, str):
            continue
        stamp = _safe_int(value, 0)
        if stamp > 0:
            result[code] = stamp
    return result


# --------------------------------------------------------------------- 每日折扣

# 折扣档位：(折扣百分比, 文案)
DISCOUNT_TIERS: tuple[tuple[int, str], ...] = (
    (0, "今日无折扣，明天再来看看。"),
    (10, "今日全场 9 折！"),
    (15, "今日全场 85 折！"),
    (20, "今日全场 8 折，手快有！"),
    (30, "今日限时 7 折，全场疯抢！"),
)


def daily_discount(session_key: str, day: str) -> int:
    """按「会话 + 日期」确定性地算当天折扣。

    同一天同一群结果稳定，换群或换天都会变；约 1/3 概率无折扣。

    Args:
        session_key: 会话标识。
        day: 日期字符串。

    Returns:
        折扣百分点（0 / 10 / 15 / 20 / 30）。
    """
    seed = f"discount:{session_key}:{day}"
    h = 0x811C9DC5
    for ch in seed:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    # 权重：0% 占 26%，其余按 10/15/20/30 递减
    bucket = h % 100
    if bucket < 26:
        tier = 0
    elif bucket < 62:
        tier = 1
    elif bucket < 84:
        tier = 2
    elif bucket < 96:
        tier = 3
    else:
        tier = 4
    return DISCOUNT_TIERS[tier][0]


def discount_text(percent: int) -> str:
    """折扣百分点转文案。

    Args:
        percent: 折扣百分点。

    Returns:
        文案。
    """
    for value, text in DISCOUNT_TIERS:
        if value == _clamp_int(percent, 0, 30, 0):
            return text
    return DISCOUNT_TIERS[0][1]


def discounted_price(price: int, percent: int) -> int:
    """计算折后价格（向下取整，最低 1）。

    Args:
        price: 原价。
        percent: 折扣百分点。

    Returns:
        折后价格。
    """
    price = max(0, _safe_int(price))
    percent = _clamp_int(percent, 0, 90, 0)
    return max(1, price * (100 - percent) // 100) if price else 0


# --------------------------------------------------------------------- 转生

# 转生所需最低等级
REBIRTH_MIN_LEVEL = 15
# 每次转生提供的收益加成（乘法叠加）
REBIRTH_BONUS_PER_LEVEL = 0.05
# 转生等级上限
REBIRTH_MAX = 20


def rebirth_bonus(rebirth: int) -> float:
    """计算转生带来的收益加成倍率。

    Args:
        rebirth: 转生次数。

    Returns:
        形如 ``1.25`` 的倍率（1 表示无加成）。
    """
    times = _clamp_int(rebirth, 0, REBIRTH_MAX, 0)
    return round(1.0 + times * REBIRTH_BONUS_PER_LEVEL, 4)


def apply_bonus(amount: int, rebirth: int) -> int:
    """把转生加成作用到一笔奖励上。

    Args:
        amount: 原始奖励。
        rebirth: 转生次数。

    Returns:
        加成后的奖励（至少 1，若原始奖励为正）。
    """
    base = _safe_int(amount)
    if base <= 0:
        return 0
    return max(1, int(base * rebirth_bonus(rebirth)))


def can_rebirth(level: int, rebirth: int) -> bool:
    """判断是否满足转生条件。

    Args:
        level: 当前等级。
        rebirth: 已转生次数。

    Returns:
        是否可转生。
    """
    return (
        _clamp_int(level, 0, 999, 0) >= REBIRTH_MIN_LEVEL
        and _clamp_int(rebirth, 0, REBIRTH_MAX, 0) < REBIRTH_MAX
    )


def rebirth_reset(user: dict) -> tuple[bool, str]:
    """执行转生：清空积分与当日进度，保留统计与收藏。

    Args:
        user: 用户档案（就地更新）。

    Returns:
        ``(是否成功, 提示)``。
    """
    from .games import level_of

    level, _inner, _need, _total = level_of(user)
    times = _clamp_int(user.get("rebirth", 0), 0, REBIRTH_MAX, 0)
    if times >= REBIRTH_MAX:
        return False, f"已经转生 {REBIRTH_MAX} 次啦，到顶了。"
    if level < REBIRTH_MIN_LEVEL:
        return False, f"转生需要 Lv.{REBIRTH_MIN_LEVEL}，你现在是 Lv.{level}。"

    user["rebirth"] = times + 1
    user["balance"] = 0
    user["title"] = ""
    return True, (
        f"🔄 转生成功！第 {times + 1} 次转生，"
        f"积分与称号已重置，收益加成提升至 ×{rebirth_bonus(times + 1):.2f}。"
        f"（统计数据与成就全部保留）"
    )

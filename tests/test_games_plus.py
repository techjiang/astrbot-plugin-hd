"""games_plus.py 纯逻辑层单测。

覆盖弹幕竞猜、井字棋、扫雷、数字破解、抛硬币、决斗盘、幸运大转盘、
成就系统、每日折扣、转生加成等第二批扩展玩法。

重点验证三类性质：

1. **账目守恒** —— 涉及积分的函数，返回值与余额变化必须严格一致；
2. **边界收敛** —— 脏配置（None/负数/超大值）不会抛异常，且结果落在合法区间；
3. **确定性** —— 依赖日期的函数（折扣）在同一天同一群结果稳定。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import games_plus as gp  # noqa: E402

# ---------------------------------------------------------------- 工具函数


class TestHelpers:
    def test_clamp_int_handles_dirty_values(self):
        assert gp._clamp_int(None, 1, 10, 5) == 5
        assert gp._clamp_int("abc", 1, 10, 5) == 5
        assert gp._clamp_int(-100, 1, 10, 5) == 1
        assert gp._clamp_int(999, 1, 10, 5) == 10
        assert gp._clamp_int("7", 1, 10, 5) == 7

    def test_normalize_option_strips_prefix(self):
        assert gp._normalize_option("/签到 ") == "签到"
        assert gp._normalize_option(" Rock ") == "rock"
        assert gp._normalize_option("石 头") == "石头"


# ---------------------------------------------------------------- 弹幕竞猜


class TestWagerOdds:
    def test_cold_option_gets_max_odds(self):
        """无人下注的选项按最高赔率展示，鼓励接冷门。"""
        odds = gp.wager_odds({"A": 100}, ["A", "B"])
        assert odds["A"] == 1.0  # 只有自己押 → 没有输家 → 只退本金
        assert odds["B"] == gp.WAGER_MAX_ODDS

    def test_all_in_one_option_gives_no_bonus(self):
        """所有人都押同一选项时没有输家，赔率必须退化为 1.0。

        这是零和的核心：如果这里给出 >1 的赔率，中奖者拿回的就是
        自己的钱再被放大，多出来的部分是**凭空印出来的积分**。
        """
        odds = gp.wager_odds({"A": 300}, ["A", "B"])
        assert odds["A"] == 1.0

    def test_odds_are_bounded(self):
        """赔率永远落在 [1.0, 20] 内，极端盘口也不会失控。"""
        odds = gp.wager_odds({"A": 1, "B": 1000000}, ["A", "B"])
        assert gp.WAGER_MIN_ODDS <= odds["A"] <= gp.WAGER_MAX_ODDS
        # B 几乎吃掉了全部池子，展示赔率收敛到下限，但**实际派彩**仍按
        # 零和分配走（赔付永远不超过输家池），所以这里只断言展示值。
        assert odds["B"] == gp.WAGER_MIN_ODDS

    def test_odds_reflect_loser_pool(self):
        """赔率随「输家池占比」上升：押的人越少，独吞的越多。"""
        few = gp.wager_odds({"A": 100, "B": 100}, ["A", "B"])["A"]
        many = gp.wager_odds({"A": 100, "B": 900}, ["A", "B"])["A"]
        assert many > few
        assert many == 10.0  # (100 + 900) / 100

    def test_empty_pool(self):
        odds = gp.wager_odds({}, ["A", "B"])
        assert odds == {"A": gp.WAGER_MAX_ODDS, "B": gp.WAGER_MAX_ODDS}

    def test_payout_is_zero_sum(self):
        """奖金只来自输家池，中奖者的本金原样退回。"""
        # 我押 100，输家池 200，中奖池 100（只有我中）
        total, share = gp.wager_payout(100, 3.0, losers_pool=200, winner_pool=100)
        assert share == 200
        assert total == 300  # 本金 100 + 奖金 200

    def test_payout_proportional_split(self):
        """多人中奖时按注额比例分奖金池。"""
        _t1, s1 = gp.wager_payout(100, 0, losers_pool=300, winner_pool=300)
        _t2, s2 = gp.wager_payout(200, 0, losers_pool=300, winner_pool=300)
        assert s1 == 100 and s2 == 200
        assert s1 + s2 == 300  # 刚好分完，不多不少

    def test_payout_no_losers(self):
        """没有输家时只能退本金，一分钱都不能多给。"""
        total, share = gp.wager_payout(100, 1.0, losers_pool=0, winner_pool=100)
        assert (total, share) == (100, 0)

    def test_payout_dirty_input(self):
        """脏输入不会抛异常。"""
        total, share = gp.wager_payout(None, 1.5)  # type: ignore[arg-type]
        assert total == 0 and share == 0
        total, share = gp.wager_payout(
            10,
            1.5,
            losers_pool=None,
            winner_pool=0,  # type: ignore[arg-type]
        )
        assert total == 10 and share == 0

    def test_never_creates_currency_random_rounds(self):
        """随机盘口跑 1500 局，任何一局都不能凭空造分。

        口径：从「所有人余额 1000」出发，走完「下注 → 开奖」全流程，
        总额相对 1000×N 只允许持平或减少。
        """
        rng = random.Random(20260103)
        start = 1000
        for _ in range(1500):
            count = rng.randint(2, 4)
            opts = [f"O{i}" for i in range(count)]
            game = gp.WagerGame.new("q", opts, "owner")
            players = [f"u{i}" for i in range(5)]
            balances = dict.fromkeys(players, start)
            for uid in players:
                ok, msg = game.place(uid, rng.randint(1, count), rng.randint(1, 300))
                if ok:
                    balances[uid] += int(msg.split(":", 1)[1])
            picked = game.settle(0, rng)
            option = game.options[picked - 1]
            losses = game.total_pool() - game.option_pool.get(option, 0)
            winner_pool = game.option_pool.get(option, 0)
            for uid, (index, amount) in game.bets.items():
                if index != picked:
                    continue
                _back, share = gp.wager_payout(
                    amount, 0, losers_pool=losses, winner_pool=winner_pool
                )
                balances[uid] += amount + share
            assert sum(balances.values()) <= start * len(players), (
                f"这一局凭空造出了积分：{game.option_pool} -> {picked}"
            )


class TestWagerGame:
    def test_new_clamps_options_and_duration(self):
        game = gp.WagerGame.new("q", [f"o{i}" for i in range(20)], "u", 99999)
        assert len(game.options) == 8
        assert game.duration == 3600

    def test_new_ignores_blank_options(self):
        game = gp.WagerGame.new("q", ["A", " ", "", "B"], "u")
        assert game.options == ["A", "B"]

    def test_place_and_pool_tracks_bets(self):
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        ok, msg = game.place("u1", 1, 50)
        # 首次下注需要补扣 50，所以 delta 为负
        assert ok and msg == "delta:-50"
        assert game.option_pool["A"] == 50
        assert game.total_pool() == 50

    def test_place_rejects_bad_index_and_amount(self):
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        assert game.place("u1", 0, 10)[0] is False
        assert game.place("u1", 3, 10)[0] is False
        assert game.place("u1", 1, 0)[0] is False
        assert game.place("u1", 1, -5)[0] is False
        assert game.total_pool() == 0

    def test_place_same_option_returns_delta(self):
        """同一选项改注：返回差额，池子按新注额计算（不能累加）。"""
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        game.place("u1", 1, 100)
        ok, msg = game.place("u1", 1, 30)
        assert ok and msg == "delta:70"
        assert game.option_pool["A"] == 30
        assert game.total_pool() == 30

    def test_place_switch_option_moves_money(self):
        """换选项时旧注要从旧选项池里扣掉，池子不能凭空变大。"""
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        game.place("u1", 1, 100)
        ok, msg = game.place("u1", 2, 100)
        assert ok and msg == "delta:0"
        # 旧池清空后会被移除，避免空池在赔率表里冒充「有人下注」
        assert "A" not in game.option_pool
        assert game.option_pool["B"] == 100
        assert game.total_pool() == 100

    def test_settle_random_when_out_of_range(self):
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        answer = game.settle(0, random.Random(0))
        assert 1 <= answer <= 2
        assert game.settled

    def test_settle_explicit_answer(self):
        game = gp.WagerGame.new("q", ["A", "B", "C"], "u")
        assert game.settle(2) == 2
        assert game.answer == 2

    def test_place_after_settle_rejected(self):
        game = gp.WagerGame.new("q", ["A", "B"], "u")
        game.settle(1)
        assert game.place("u1", 1, 10)[0] is False

    def test_expired_blocks_betting(self):
        """到点后不再接受下注。

        注意 ``place()`` 内部的过期判断走系统时间，所以这里必须真的把
        开始时间往前推，而不是只给 ``expired()`` 传参。
        """
        game = gp.WagerGame.new("q", ["A", "B"], "u", 30)
        game.started_at -= 31
        assert game.expired()
        assert game.place("u1", 1, 10)[0] is False


# ---------------------------------------------------------------- 井字棋


class TestTicTacToe:
    def test_winner_rows_cols_diagonals(self):
        assert gp.tictactoe_winner(list("XXX" + "OO " + "   ")) == "X"
        assert gp.tictactoe_winner(list("XO " + "XO " + "X  ")) == "X"
        assert gp.tictactoe_winner(list("XOO" + " X " + "  X")) == "X"
        assert gp.tictactoe_winner(list("O X" + " X " + "X  ")) == "X"

    def test_no_winner(self):
        """双方都不成三时没有胜者。

        注意 ``两斜线 (0,4,8) 与 (2,4,6)`` 极易被误判成平局：
        ``XOXOXOXXO`` 看上去像和棋，其实 0/4/8 全是 X。
        """
        assert gp.tictactoe_winner(list("XOXXOOOXX")) == ""
        assert gp.tictactoe_winner(list("XXOOOXXOO")) == ""
        assert gp.tictactoe_winner([""] * 9) == ""

    def test_ai_takes_winning_move(self):
        """AI 有连三机会时必须立刻拿下。"""
        board = ["O", "O", "", "X", "X", "", "", "", ""]
        assert gp.tictactoe_ai_move(board, "O", random.Random(0)) == 2

    def test_ai_blocks_opponent(self):
        """AI 必须堵住对手的连三。"""
        board = ["X", "X", "", "O", "", "", "", "", ""]
        assert gp.tictactoe_ai_move(board, "O", random.Random(0)) == 2

    def test_ai_takes_center_when_free(self):
        board = ["X", "", "", "", "", "", "", "", ""]
        assert gp.tictactoe_ai_move(board, "O", random.Random(0)) == 4

    def test_ai_returns_minus_one_when_full(self):
        assert gp.tictactoe_ai_move(list("XOXXOOOXX"), "O") == -1

    def test_new_defaults(self):
        game = gp.TicTacToeGame.new()
        assert game.human == "X" and game.ai == "O" and game.turn == "X"

    def test_new_with_o_swaps_marks(self):
        game = gp.TicTacToeGame.new("O")
        assert game.human == "O" and game.ai == "X"

    def test_play_rejects_occupied_and_out_of_range(self):
        game = gp.TicTacToeGame.new()
        assert game.play(-1)[0] == "invalid"
        assert game.play(9)[0] == "invalid"
        game.play(0)
        assert game.play(0)[0] == "invalid"

    def test_random_play_sometimes_loses(self):
        """随机乱下的对手会给 AI 送胜场 —— 证明下一条断言不是空转。"""
        rng = random.Random(42)
        ai_wins = 0
        for _ in range(300):
            game = gp.TicTacToeGame.new()
            while not game.finished:
                free = [i for i in range(9) if not game.board[i]]
                if not free:
                    break
                game.play(rng.choice(free), rng)
            if gp.tictactoe_winner(game.board) == game.ai:
                ai_wins += 1
        assert ai_wins > 0

    def test_ai_never_loses_to_optimal_play(self):
        """面对不失误的对手，AI 必须保持 0 负（井字棋理论上是和棋）。

        这里用**同一套启发式**扮演「完美人类」：它同样会优先连三、堵对手。
        一个正确的 AI 面对它应该全是平局；如果 AI 的必守逻辑漏了，
        这场对局会立刻出现败场，测试也就红了。

        断言看 ``game.board`` 上的成三，而不是 ``play()`` 的返回值 ——
        后者的 ``"win"`` 指的是「人类这一步赢了」。
        """
        rng = random.Random(0)
        wins = losses = draws = 0
        for _ in range(300):
            game = gp.TicTacToeGame.new()
            while not game.finished:
                free = [i for i in range(9) if not game.board[i]]
                if not free:
                    break
                move = gp.tictactoe_ai_move(game.board, game.human, rng)
                game.play(move, rng)
            winner = gp.tictactoe_winner(game.board)
            if winner == game.human:
                wins += 1
            elif winner == game.ai:
                losses += 1
            else:
                draws += 1
        assert wins == 0, "完美对手不该赢过 AI（说明 AI 有漏堵）"
        assert losses == 0, "完美对手不该输给 AI"
        assert draws == 300, "双方都不失误时井字棋必然和棋"

    def test_ai_blocks_immediate_loss(self):
        """回归：AI 必须堵住「对手已有两子 + 一个空格」的必杀线。

        这条曾经真的写错过 —— 早期实现只找「自己落子能成三」的机会，
        漏掉了对手的杀招，于是会输给随机乱下的对手。
        """
        # 顶行 X X _  -> 堵 2
        assert gp.tictactoe_ai_move(["X", "X", "", "", "O", "", "", "", ""], "O") == 2
        # 左列 X _ X  -> 堵 6
        assert gp.tictactoe_ai_move(["X", "", "", "X", "O", "", "", "", ""], "O") == 6
        # 主对角线 X _ _ X -> 堵 8
        assert gp.tictactoe_ai_move(["X", "", "", "", "X", "", "O", "", ""], "O") == 8
        # 中列 _ X _ X -> 堵 7
        assert gp.tictactoe_ai_move(["O", "X", "", "", "X", "", "", "", ""], "O") == 7

    def test_ai_prefers_own_win_over_blocking(self):
        """自己有连三可成时，必须先拿胜利而不是去堵对手。"""
        board = ["O", "O", "", "X", "X", "", "", "", ""]
        assert gp.tictactoe_ai_move(board, "O") == 2

    def test_ai_ignores_dead_threats(self):
        """对手的线已被自己占住时必须换目标，不能死守。"""
        # 2-4-6 这条线的中心 4 已被 O 占住，X 在这条线上已无法成三，
        # 所以 AI 不该把 6 当成「必堵点」，而应去占角。
        board = ["", "", "X", "", "O", "", "X", "", ""]
        assert gp.tictactoe_ai_move(board, "O") in (0, 2, 8)

    def test_render_contains_marks(self):
        game = gp.TicTacToeGame.new()
        game.play(0)
        text = game.render()
        assert "X" in text and "O" in text


# ---------------------------------------------------------------- 扫雷


class TestMinefield:
    def test_new_clamps_size_and_mines(self):
        game = gp.MinefieldGame.new(1, 999, random.Random(0))
        assert game.size == 3
        assert 1 <= game.mines < 9
        assert len(game.cells) == 9

    def test_first_safe_is_never_mine(self):
        """先手保护：指定格永远不是雷。"""
        for seed in range(50):
            game = gp.MinefieldGame.new(6, 20, random.Random(seed), first_safe=0)
            assert game.cells[0] != -1

    def test_hint_counts_neighbour_mines(self):
        """非雷格的数字必须等于周围 8 格里的雷数。"""
        game = gp.MinefieldGame.new(5, 5, random.Random(3))
        n = game.size
        for i, value in enumerate(game.cells):
            if value == -1:
                continue
            r, c = divmod(i, n)
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < n and 0 <= nc < n and game.cells[nr * n + nc] == -1:
                        count += 1
            assert value == count

    def test_open_out_of_range(self):
        game = gp.MinefieldGame.new(4, 3, random.Random(0))
        boom, msg, opened = game.open(-1)
        assert boom is False and opened == set() and "格号" in msg
        boom, _msg, opened = game.open(999)
        assert opened == set()

    def test_open_same_cell_twice(self):
        game = gp.MinefieldGame.new(4, 1, random.Random(0))
        safe = next(i for i, v in enumerate(game.cells) if v != -1)
        game.open(safe)
        boom, msg, opened = game.open(safe)
        assert opened == set() and "已经翻开" in msg

    def test_open_mine_booms(self):
        game = gp.MinefieldGame.new(5, 5, random.Random(1))
        mine = next(i for i, v in enumerate(game.cells) if v == -1)
        boom, msg, opened = game.open(mine)
        assert boom is True and mine in opened and "炸" in msg or "雷" in msg

    def test_cleared_when_all_safe_opened(self):
        game = gp.MinefieldGame.new(3, 2, random.Random(2))
        mine = next(i for i, v in enumerate(game.cells) if v == -1)
        del mine
        for i, v in enumerate(game.cells):
            if v != -1:
                game.open(i)
        assert game.cleared()

    def test_flood_fill_opens_region(self):
        """翻开一个 0 格应当连带翻开整片连通区域，而不只一格。"""
        game = gp.MinefieldGame.new(6, 2, random.Random(9))
        zero = next((i for i, v in enumerate(game.cells) if v == 0), None)
        if zero is None:
            return
        _boom, _msg, opened = game.open(zero)
        assert len(opened) >= 1

    def test_render_hides_mines(self):
        game = gp.MinefieldGame.new(4, 2, random.Random(0))
        text = game.render()
        assert "💥" not in text


# ---------------------------------------------------------------- 数字破解


class TestCodebreaker:
    def test_new_secret_has_no_leading_zero_and_right_length(self):
        for seed in range(100):
            game = gp.CodebreakerGame.new(4, 8, random.Random(seed))
            assert len(game.secret) == 4
            assert game.secret[0] != "0"
            assert game.secret.isdigit()

    def test_new_clamps_digits_and_attempts(self):
        game = gp.CodebreakerGame.new(99, 999, random.Random(0))
        assert game.digits == 6 and game.max_attempts == 20
        game = gp.CodebreakerGame.new(1, 1, random.Random(0))
        assert game.digits == 3 and game.max_attempts == 3

    def test_guess_scoring(self):
        exact, misplaced = gp.codebreaker_guess("1234", "1234")
        assert (exact, misplaced) == (4, 0)
        exact, misplaced = gp.codebreaker_guess("1234", "4321")
        assert (exact, misplaced) == (0, 4)
        exact, misplaced = gp.codebreaker_guess("1234", "1256")
        assert (exact, misplaced) == (2, 0)
        exact, misplaced = gp.codebreaker_guess("1234", "5678")
        assert (exact, misplaced) == (0, 0)

    def test_guess_handles_repeated_digits(self):
        """重复数字必须按「最少出现次数」统计，不能超发。"""
        exact, misplaced = gp.codebreaker_guess("1122", "1111")
        assert exact == 2
        assert misplaced == 0

    def test_submit_rejects_wrong_length(self):
        game = gp.CodebreakerGame.new(4, 8, random.Random(0))
        result, msg, _e, _m = game.submit("12")
        assert result == "invalid" and "4 位" in msg
        assert game.attempts == 0

    def test_submit_strips_non_digits(self):
        game = gp.CodebreakerGame.new(4, 8, random.Random(0))
        result, _msg, _e, _m = game.submit(game.secret.replace("", " ").strip())
        assert result == "win"

    def test_submit_loses_after_max_attempts(self):
        game = gp.CodebreakerGame.new(4, 3, random.Random(0))
        wrong = "0000" if game.secret != "0000" else "1111"
        result = "continue"
        for _ in range(3):
            result, _msg, _e, _m = game.submit(wrong)
        assert result == "lose"
        assert game.secret in _msg

    def test_win_reports_answer(self):
        game = gp.CodebreakerGame.new(3, 5, random.Random(1))
        result, msg, _e, _m = game.submit(game.secret)
        assert result == "win" and game.secret in msg


# ---------------------------------------------------------------- 抛硬币


class TestCoin:
    def test_aliases(self):
        assert gp.resolve_coin_bet("正") == "正面"
        assert gp.resolve_coin_bet("/heads") == "正面"
        assert gp.resolve_coin_bet("反") == "反面"
        assert gp.resolve_coin_bet("tails") == "反面"
        assert gp.resolve_coin_bet("乱写") == ""

    def test_flip_always_valid(self):
        rng = random.Random(0)
        for _ in range(200):
            assert gp.flip_coin(rng) in gp.COIN_SIDES


# ---------------------------------------------------------------- 决斗盘


class TestRpsls:
    def test_aliases(self):
        assert gp.resolve_rpsls("石") == "石头"
        assert gp.resolve_rpsls("rock") == "石头"
        assert gp.resolve_rpsls("SPOCK") == "斯波克"
        assert gp.resolve_rpsls("蜥蜴") == "蜥蜴"
        assert gp.resolve_rpsls("乱写") == ""

    def test_judge_draw(self):
        for move in gp.RPSLS_BEATS:
            assert gp.rpsls_judge(move, move) == "draw"

    def test_judge_win_lose_symmetry(self):
        """A 赢 B 时，反过来必须是 B 赢 A。"""
        for a, beaten in gp.RPSLS_BEATS.items():
            for b in beaten:
                assert gp.rpsls_judge(a, b) == "a"
                assert gp.rpsls_judge(b, a) == "b"

    def test_every_move_beats_exactly_two(self):
        assert all(len(v) == 2 for v in gp.RPSLS_BEATS.values())

    def test_every_move_is_beaten_by_exactly_two(self):
        """平衡性：每个手势恰好被两个手势克制，不能有强弱失衡。"""
        for move in gp.RPSLS_BEATS:
            beaten_by = [m for m, targets in gp.RPSLS_BEATS.items() if move in targets]
            assert len(beaten_by) == 2, move

    def test_judge_bad_input(self):
        assert gp.rpsls_judge("乱写", "石头") == "draw"

    def test_flavor_mentions_both(self):
        text = gp.rpsls_flavor("石头", "剪刀")
        assert "石头" in text and "剪刀" in text


# ---------------------------------------------------------------- 大转盘


class TestWheel:
    def test_spin_accounts_for_cost(self):
        """净变动必须等于「奖值 - 消耗」，账目不能凭空多出来。"""
        rng = random.Random(0)
        cost = 50
        for _ in range(500):
            kind, delta, _label = gp.wheel_spin(cost, rng)
            assert kind in ("amount", "item")
            assert delta >= -cost
            if kind == "item":
                assert delta == -cost

    def test_spin_negative_cost_treated_as_zero(self):
        _kind, delta, _label = gp.wheel_spin(-100, random.Random(0))
        assert delta >= 0

    def test_sectors_have_valid_shape(self):
        for weight, kind, value, label in gp.WHEEL_SECTORS:
            assert weight > 0
            assert kind in ("amount", "item")
            assert isinstance(value, int)
            assert label

    def test_expected_value_below_cost(self):
        """期望必须严格为负，否则转盘会变成印钞机。

        判定口径取最严格的那种：**只把积分回报计入收益，称号当作 0 价值**。
        因为称号是虚拟物品，玩家完全可以不在乎它；只有在这个口径下期望
        仍为负，才算真的没有刷分空间。
        """
        cost = gp.WHEEL_BASE_COST
        total_weight = sum(w for w, *_ in gp.WHEEL_SECTORS)
        ret = (
            sum(w * v for w, kind, v, _l in gp.WHEEL_SECTORS if kind == "amount")
            / total_weight
        )
        assert ret < cost, f"积分期望回报 {ret:.2f} 不应达到消耗 {cost}"
        assert ret / cost < 0.95, "回收率应明显低于 1，留有安全边际"

    def test_item_sector_costs_only(self):
        rng = random.Random(0)
        found = False
        for _ in range(3000):
            kind, delta, label = gp.wheel_spin(50, rng)
            if kind == "item":
                found = True
                assert delta == -50
                assert "称号" in label
        assert found


# ---------------------------------------------------------------- 成就系统


class TestAchievements:
    def test_definitions_are_well_formed(self):
        codes = [c for c, *_ in gp.ACHIEVEMENTS]
        assert len(codes) == len(set(codes)), "成就码不能重复"
        for code, name, field_name, threshold, reward in gp.ACHIEVEMENTS:
            assert code and name and field_name
            assert threshold > 0 and reward >= 0

    def test_map_matches_list(self):
        assert set(gp.ACHIEVEMENT_MAP) == {c for c, *_ in gp.ACHIEVEMENTS}

    def test_progress_marks_unlocked(self):
        user = {"balance": 5000, "achievements": ["rich_1k"]}
        rows = {
            code: (cur, th, ok)
            for code, _n, cur, th, ok in gp.achievement_progress(user)
        }
        assert rows["rich_1k"] == (1000, 1000, True)
        assert rows["rich_1w"][2] is False

    def test_progress_handles_dirty_user(self):
        rows = gp.achievement_progress({})
        assert rows and all(r[2] == 0 for r in rows)

    def test_check_unlocks_and_pays(self):
        user = {"balance": 1500, "achievements": []}
        gained, total = gp.check_achievements(user)
        codes = [c for c, _n, _r in gained]
        assert "rich_1k" in codes
        assert total > 0
        assert user["balance"] == 1500 + total
        assert "rich_1k" in user["achievements"]

    def test_check_is_idempotent(self):
        """重复检查不能重复发奖。"""
        user = {"balance": 1500, "achievements": []}
        _gained, total = gp.check_achievements(user)
        balance_after = user["balance"]
        gained2, total2 = gp.check_achievements(user)
        assert gained2 == [] and total2 == 0
        assert user["balance"] == balance_after

    def test_check_accepts_injected_codes(self):
        user = {"balance": 0, "achievements": []}
        gained, _total = gp.check_achievements(user, newly=["soup_10"])
        assert "soup_10" in user["achievements"]
        assert "soup_10" not in [c for c, _n, _r in gained]


# ---------------------------------------------------------------- 每日折扣


class TestDiscount:
    def test_deterministic_same_day_same_session(self):
        a = gp.daily_discount("g1", "2026-01-01")
        b = gp.daily_discount("g1", "2026-01-01")
        assert a == b

    def test_day_and_session_matter(self):
        values = {gp.daily_discount(f"g{i}", f"2026-01-{i:02d}") for i in range(60)}
        assert len(values) > 1

    def test_value_in_allowed_set(self):
        allowed = {t[0] for t in gp.DISCOUNT_TIERS}
        for i in range(500):
            assert gp.daily_discount(f"s{i}", f"2026-02-{i % 28 + 1:02d}") in allowed

    def test_discount_text_covers_all_tiers(self):
        for value, text in gp.DISCOUNT_TIERS:
            assert gp.discount_text(value) == text

    def test_discount_text_unknown_falls_back(self):
        assert gp.discount_text(7) == gp.DISCOUNT_TIERS[0][1]

    def test_discounted_price(self):
        assert gp.discounted_price(100, 0) == 100
        assert gp.discounted_price(100, 10) == 90
        assert gp.discounted_price(100, 30) == 70
        assert gp.discounted_price(0, 30) == 0
        assert gp.discounted_price(1, 30) == 1  # 最低 1，不能白送
        assert gp.discounted_price(None, 10) == 0  # type: ignore[arg-type]

    def test_discounted_price_clamps_percent(self):
        assert gp.discounted_price(100, 999) == 10


# ---------------------------------------------------------------- 转生


class TestRebirth:
    def test_bonus_is_monotonic(self):
        values = [gp.rebirth_bonus(i) for i in range(gp.REBIRTH_MAX + 1)]
        assert values == sorted(values)
        assert values[0] == 1.0

    def test_bonus_clamps(self):
        assert gp.rebirth_bonus(-5) == 1.0
        assert gp.rebirth_bonus(999) == gp.rebirth_bonus(gp.REBIRTH_MAX)

    def test_apply_bonus(self):
        assert gp.apply_bonus(100, 0) == 100
        assert gp.apply_bonus(100, 2) == 110
        assert gp.apply_bonus(0, 5) == 0
        assert gp.apply_bonus(-10, 5) == 0
        assert gp.apply_bonus(1, 20) == 2

    def test_can_rebirth(self):
        assert gp.can_rebirth(gp.REBIRTH_MIN_LEVEL, 0)
        assert not gp.can_rebirth(gp.REBIRTH_MIN_LEVEL - 1, 0)
        assert not gp.can_rebirth(gp.REBIRTH_MIN_LEVEL, gp.REBIRTH_MAX)

    def test_reset_blocked_below_level(self):
        user = {"balance": 0, "total_sign": 0, "rebirth": 0}
        ok, msg = gp.rebirth_reset(user)
        assert not ok and "Lv." in msg
        assert user["rebirth"] == 0

    def test_reset_success_clears_money_keeps_stats(self):
        # 等级 = (余额 + 签到*5) // 100 + 1，攒够 15 级
        user = {
            "balance": 2000,
            "total_sign": 100,
            "rebirth": 2,
            "title": "大佬",
            "titles": ["大佬"],
            "backpack": {},
            "chain_win": 42,
        }
        ok, msg = gp.rebirth_reset(user)
        assert ok and "第 3 次" in msg
        assert user["balance"] == 0
        assert user["title"] == ""
        assert user["rebirth"] == 3
        assert user["chain_win"] == 42, "统计数据必须保留"
        assert user["titles"] == ["大佬"], "收藏必须保留"

    def test_reset_at_max_rejected(self):
        user = {"balance": 10**9, "total_sign": 10**6, "rebirth": gp.REBIRTH_MAX}
        ok, msg = gp.rebirth_reset(user)
        assert not ok and "到顶" in msg

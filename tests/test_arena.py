"""第四批扩展玩法（``games_arena``）的单测。

沿用前几批的验证策略：**先锁经济不变量，再测交互细节**。

本模块的重点断言是「不能凭空虚增积分」：

- 大富翁走完一圈的净变化必须 <= 0（否则每掷一次骰就是一次印钞）；
- 拍卖与拔河只做转移 / 有限发放，不产生新的积分池。

这类断言的价值在编写过程中就体现过：第一版棋盘奖励导向配置
（一圈 +50）被 ``test_monopoly_lap_is_not_a_printer`` 当场拦下。
"""

from __future__ import annotations

import random

from astrbot_plugin_hudong import games_arena as ga

# --------------------------------------------------------------------- 拍卖


class TestAuction:
    def _item(self):
        return ga.AuctionItem("title_ouhuang", "称号·欧皇", 300, "title")

    def test_min_next_bid_follows_increment(self):
        a = ga.Auction.new(self._item(), start_price=10, min_increment=5)
        assert a.min_next_bid() == 10
        a.place_bid("u1", 10)
        assert a.min_next_bid() == 15

    def test_first_bid_cannot_be_below_start(self):
        a = ga.Auction.new(self._item(), start_price=50)
        assert a.place_bid("u1", 49)[0] is False
        assert a.place_bid("u1", 50)[0] is True

    def test_bid_must_strictly_exceed_top(self):
        a = ga.Auction.new(self._item(), start_price=10)
        a.place_bid("u1", 30)
        assert a.place_bid("u2", 30)[0] is False
        assert a.place_bid("u2", 31)[0] is True
        assert a.top_bid == 31
        assert a.top_bidder == "u2"

    def test_owner_cannot_bid_on_own_auction(self):
        a = ga.Auction.new(self._item(), start_price=1, owner="me")
        assert a.place_bid("me", 100)[0] is False

    def test_settle_twice_is_rejected(self):
        a = ga.Auction.new(self._item(), start_price=1)
        a.place_bid("u1", 10)
        a.settle()
        assert a.settle()["ok"] is False

    def test_settle_without_bids_is_unsold(self):
        a = ga.Auction.new(self._item(), start_price=10)
        r = a.settle()
        assert r["ok"] is True and r["sold"] is False
        assert a.winner == ""

    def test_winner_pays_exactly_his_bid(self):
        a = ga.Auction.new(self._item(), start_price=1, min_increment=1)
        a.place_bid("u1", 10)
        a.place_bid("u2", 25)
        r = a.settle()
        assert r["winner"] == "u2"
        assert r["price"] == 25

    def test_dirty_input_does_not_crash(self):
        a = ga.Auction.new(self._item(), start_price="x", min_increment=None)
        for bad in (None, "abc", -5, 0, [], {}):
            ok, msg = a.place_bid("u1", bad)
            assert isinstance(ok, bool) and isinstance(msg, str)

    def test_bid_cap_prevents_unbounded_growth(self):
        a = ga.Auction.new(self._item(), start_price=0, min_increment=1)
        for i in range(ga.AUCTION_MAX_BIDS + 50):
            a.place_bid("u1", i + 1)
        assert len(a.bid_order) <= ga.AUCTION_MAX_BIDS


# --------------------------------------------------------------------- 拔河


class TestTugOfWar:
    def test_higher_power_wins(self):
        t = ga.TugOfWar.new(["A", "B"], prizes=30)
        t.join("A", "u1", 100)
        t.join("B", "u2", 50)
        r = t.settle()
        assert r["winner"] == "A"
        assert r["winners"] == ["u1"]

    def test_tie_grants_no_prize(self):
        t = ga.TugOfWar.new(["A", "B"], prizes=30)
        t.join("A", "u1", 50)
        t.join("B", "u2", 50)
        r = t.settle()
        assert r["tie"] is True
        assert r["winners"] == []

    def test_cannot_switch_team(self):
        t = ga.TugOfWar.new(["A", "B"])
        t.join("A", "u1", 10)
        ok, msg = t.join("B", "u1", 10)
        assert ok is False and "换队" in msg

    def test_unknown_team_rejected(self):
        t = ga.TugOfWar.new(["A", "B"])
        assert t.join("C", "u1", 10)[0] is False

    def test_non_positive_power_rejected(self):
        t = ga.TugOfWar.new(["A", "B"])
        assert t.join("A", "u1", 0)[0] is False
        assert t.join("A", "u1", -10)[0] is False

    def test_settle_twice_is_rejected(self):
        t = ga.TugOfWar.new(["A", "B"])
        t.join("A", "u1", 10)
        t.settle()
        assert t.settle()["ok"] is False

    def test_prize_total_is_bounded_by_config(self):
        """奖金只来自配置，不随力量增长 —— 投得再多也不会多发。"""
        t = ga.TugOfWar.new(["A", "B"], prizes=30)
        for i in range(5):
            t.join("A", f"u{i}", 10**6)
        r = t.settle()
        assert len(r["winners"]) == 5
        assert t.prizes == 30  # 与投入力量无关

    def test_dirty_input_does_not_crash(self):
        t = ga.TugOfWar.new(["A", "B"])
        for bad in (None, "abc", -1, 10**12, [], {}):
            ok, msg = t.join("A", "u1", bad)
            assert isinstance(ok, bool) and isinstance(msg, str)


# --------------------------------------------------------------------- 知识竞速


class TestQuizRush:
    def test_correct_answer_wins(self):
        q = ga.QuizRush.new(random.Random(1))
        assert q.submit("u1", q.answers[0])[0] == "correct"
        assert q.solved_by == "u1"

    def test_wrong_then_locked(self):
        q = ga.QuizRush.new(random.Random(2))
        assert q.submit("u1", "肯定不对")[0] == "wrong"
        assert q.submit("u1", "再试一次")[0] == "locked"

    def test_attempt_cap_then_closed(self):
        """等够冷却再答，答错到上限后本题对该用户关闭。

        注意要显式推进 ``now``：连续刷屏只会一直撞在 locked 上，
        attempts 不会累加 —— 那是另一条用例覆盖的行为。
        """
        import time

        q = ga.QuizRush.new(random.Random(3))
        now = time.time()
        outcomes = []
        for _ in range(q.MAX_WRONG + 3):
            outcomes.append(q.submit("u1", "错", now=now)[0])
            now += q.WRONG_LOCK + 0.1
        assert "closed" in outcomes
        # 第 MAX_WRONG 次答错直接判 closed，所以 wrong 出现 MAX_WRONG - 1 次
        assert outcomes.count("wrong") == q.MAX_WRONG - 1
        assert outcomes.count("closed") == 4

    def test_spamming_within_lock_does_not_accumulate_attempts(self):
        """刷屏期间不累加答错次数 —— 冷却本身就是限流，不必再叠加惩罚。"""
        import time

        q = ga.QuizRush.new(random.Random(3))
        now = time.time()
        # 第一次答错 -> wrong；随后 20 次全部撞冷却
        assert q.submit("u1", "错", now=now)[0] == "wrong"
        for _ in range(20):
            assert q.submit("u1", "错", now=now)[0] == "locked"
        assert q.attempts["u1"] == 1

    def test_expired_returns_closed(self):
        q = ga.QuizRush.new(random.Random(4))
        q.started_at -= q.timeout + 10
        outcome, msg = q.submit("u1", q.answers[0])
        assert outcome == "closed" and q.answers[0] in msg

    def test_answer_normalization(self):
        """带标点、大小写、空格都应算对。"""
        assert ga.normalize_answer("  Beijing！ ") == "beijing"
        assert ga.quiz_match(("北京",), "北京。")
        assert ga.quiz_match(("h2o",), " H2O ")

    def test_solved_question_rejects_later_answers(self):
        q = ga.QuizRush.new(random.Random(5))
        q.submit("u1", q.answers[0])
        assert q.submit("u2", q.answers[0])[0] == "closed"

    def test_exclude_avoids_repeat(self):
        first = ga.QuizRush.new(random.Random(6))
        for _ in range(20):
            nxt = ga.QuizRush.new(random.Random(7), exclude=(first.question,))
            assert nxt.question != first.question

    def test_dirty_input_does_not_crash(self):
        q = ga.QuizRush.new(random.Random(8))
        for bad in (None, "", [], {}, 123):
            outcome, msg = q.submit("u1", bad)
            assert isinstance(outcome, str) and isinstance(msg, str)


# --------------------------------------------------------------------- 大富翁


class TestMonopoly:
    def test_lap_is_not_a_printer(self):
        """一圈净变化必须 <= 0 —— 这是本玩法的核心经济约束。

        第一版把奖励配得比惩罚多（一圈 +50），等于每掷一次骰就印一次钱。
        本用例是那次修复的回归守卫。
        """
        total = sum(ga.monopoly_delta(k) for k, _ in ga.MONOPOLY_TILES)
        assert total <= 0, f"大富翁一圈净收益 {total}，会成为印钞机"

    def test_long_run_is_negative(self):
        """多次掷骰的期望必须为负（通缩），否则长期能刷分。"""
        rng = random.Random(42)
        total = 0
        for _ in range(500):
            run = ga.DiceRun()
            total += run.total_delta(run.roll(rng, times=20))
        assert total < 0, f"10000 次掷骰净收益 {total}，期望应为负"

    def test_position_wraps_and_counts_laps(self):
        """位置始终落在棋盘内，且走满一圈会累加 laps。

        ``times`` 单次上限是 20，所以要多次调用才能走出多圈。
        """
        rng = random.Random(1)
        run = ga.DiceRun()
        for _ in range(10):
            run.roll(rng, times=20)
        assert 0 <= run.position < len(ga.MONOPOLY_TILES)
        assert run.laps >= 5
        assert run.steps == 200

    def test_times_is_clamped_per_call(self):
        """单次 times 上限 20 —— 防止一句指令刷出巨量结算。"""
        rng = random.Random(9)
        run = ga.DiceRun()
        assert len(run.roll(rng, times=10**9)) == 20
        assert len(run.roll(rng, times=0)) == 1

    def test_history_is_capped(self):
        """轨迹只留最近若干条，避免长期运行内存膨胀。"""
        rng = random.Random(2)
        run = ga.DiceRun()
        for _ in range(50):
            run.roll(rng, times=10)
        assert len(run.history) <= 20

    def test_step_cap(self):
        rng = random.Random(3)
        run = ga.DiceRun()
        for _ in range(2000):
            run.roll(rng, times=20)
        assert run.steps <= ga.DiceRun.MAX_STEPS

    def test_tile_lookup_wraps(self):
        n = len(ga.MONOPOLY_TILES)
        assert ga.monopoly_tile(n)[0] == ga.monopoly_tile(0)[0]
        assert ga.monopoly_tile(n * 3 + 4)[0] == ga.monopoly_tile(4)[0]

    def test_dirty_times_does_not_crash(self):
        rng = random.Random(4)
        run = ga.DiceRun()
        for bad in (None, "abc", -5, 0, 10**9, [], {}):
            assert isinstance(run.roll(rng, times=bad), list)

    def test_render_contains_total(self):
        rng = random.Random(5)
        run = ga.DiceRun()
        produced = run.roll(rng, times=3)
        text = run.render(produced)
        assert "互动币" in text and "第" in text


# --------------------------------------------------------------------- 战队


class TestSquad:
    def test_tier_increases_with_contribution(self):
        names = [ga.squad_tier(v)[0] for v in (0, 600, 3000, 9000, 50000)]
        assert names == [t[1] for t in ga.SQUAD_TIERS]

    def test_top_tier_has_no_next_threshold(self):
        _name, idx, nxt = ga.squad_tier(10**9)
        assert idx == len(ga.SQUAD_TIERS) - 1 and nxt == 0

    def test_progress_within_tier(self):
        name, inner, need = ga.squad_progress(2500)
        assert name == "🌳 精锐"
        assert inner == 500 and need == 6000

    def test_contribution_from_stats(self):
        user = {
            "total_sign": 10,
            "lottery_count": 5,
            "guess_win": 3,
            "chain_win": 2,
            "rob_win": 1,
            "tictactoe_win": 4,
            "mine_clear": 6,
        }
        # 10*2 + 5 + 3 + 2 + 1 + 4 + 6
        assert ga.squad_contribution(user) == 41

    def test_contribution_ignores_dirty_values(self):
        assert ga.squad_contribution({}) == 0
        for bad in (None, "abc", [], {}):
            assert ga.squad_contribution({"total_sign": bad}) == 0

    def test_dirty_tier_input_does_not_crash(self):
        for bad in (None, "abc", -100, 10**15, [], {}):
            assert isinstance(ga.squad_tier(bad)[0], str)

    def test_negative_contribution_clamps_to_first_tier(self):
        assert ga.squad_tier(-999)[0] == ga.SQUAD_TIERS[0][1]

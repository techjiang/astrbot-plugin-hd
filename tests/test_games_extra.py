"""games_extra.py 纯逻辑层单测。

覆盖 21 点、数字炸弹、猜谜、海龟汤、抢答、运势、商店、亲密度、礼物、
表白、PK、冷笑话等扩展玩法。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import games_extra as gx  # noqa: E402

# ---------------------------------------------------------------- 21 点


class TestBlackjack:
    def test_card_value_ace_flexible(self):
        """A 可当 11 也可当 1，自动取最优。"""
        assert gx.BlackjackGame.card_value([1, 13]) == 21
        assert gx.BlackjackGame.card_value([1, 1, 9]) == 21
        assert gx.BlackjackGame.card_value([10, 10, 5]) == 25
        assert gx.BlackjackGame.card_value([]) == 0

    def test_draw_in_range(self):
        rng = random.Random(0)
        for _ in range(200):
            assert 1 <= gx.BlackjackGame.draw(rng) <= 13

    def test_new_deals_two_cards_each(self):
        game = gx.BlackjackGame.new(100, rng=random.Random(1))
        assert len(game.player) == 2 and len(game.dealer) == 2
        assert game.bet == 100

    def test_natural_blackjack_finishes(self):
        rng = random.Random(7)
        for _ in range(500):
            game = gx.BlackjackGame.new(10, rng=rng)
            if game.finished:
                assert game.player_value() == 21
                return
        raise AssertionError("500 次都没出现起手 21 点，概率异常")

    def test_negative_bet_clamped(self):
        assert gx.BlackjackGame.new(-50).bet == 0

    def test_dealer_draws_until_17(self):
        game = gx.BlackjackGame(player=[10, 9], dealer=[2, 3], bet=10)
        result = game.stand(rng=random.Random(3))
        assert result in {"win", "lose", "push"}
        assert game.dealer_value() >= 17

    def test_bust_zero_payout(self):
        game = gx.BlackjackGame(player=[10, 10, 10], dealer=[5, 5], bet=10)
        assert game.player_value() == 30
        assert game.payout("bust") == 0

    def test_payout_rates(self):
        normal = gx.BlackjackGame(player=[10, 9], dealer=[5, 5], bet=100)
        assert normal.payout("win") == 200
        assert normal.payout("push") == 100
        assert normal.payout("lose") == 0
        blackjack = gx.BlackjackGame(player=[1, 10], dealer=[5, 5], bet=100)
        assert blackjack.payout("win") == 250

    def test_hit_returns_continue_or_terminal(self):
        outcomes = set()
        rng = random.Random(11)
        for _ in range(300):
            game = gx.BlackjackGame(player=[2, 3], dealer=[5, 5], bet=10)
            outcomes.add(game.hit(rng=rng))
        assert outcomes <= {"continue", "bust", "win", "lose", "push"}

    def test_hit_on_finished_game(self):
        game = gx.BlackjackGame(player=[10, 10, 10], dealer=[5, 5], bet=10)
        game.finished = True
        assert game.hit() == "finished"


# ---------------------------------------------------------------- 数字炸弹


class TestBomb:
    def test_target_within_bounds(self):
        rng = random.Random(0)
        for _ in range(300):
            game = gx.BombGame.new(1, 50, rng=rng)
            assert 1 <= game.target <= 50

    def test_span_auto_widened(self):
        game = gx.BombGame.new(1, 3)
        assert game.high - game.low >= 10

    def test_report_narrows_range(self):
        game = gx.BombGame(low=1, high=100, target=50)
        boom, msg = game.report(20, "u1")
        assert not boom and game.low == 21
        boom, msg = game.report(80, "u2")
        assert not boom and game.high == 80

    def test_report_boom(self):
        game = gx.BombGame(low=1, high=100, target=50)
        boom, msg = game.report(50, "u1")
        assert boom and "BOOM" in msg

    def test_out_of_range_rejected(self):
        game = gx.BombGame(low=10, high=20, target=15)
        boom, msg = game.report(5, "u1")
        assert not boom and "请报" in msg
        assert game.low == 10  # 区间未被改动

    def test_rounds_counted(self):
        game = gx.BombGame(low=1, high=100, target=50)
        game.report(10, "u1")
        game.report(20, "u2")
        assert game.rounds == 2


# ---------------------------------------------------------------- 题库


class TestRiddleSoup:
    def test_random_riddle_shape(self):
        rng = random.Random(0)
        for _ in range(50):
            q, a, hint = gx.random_riddle(rng)
            assert q and a and hint

    def test_random_soup_shape(self):
        rng = random.Random(0)
        for _ in range(50):
            title, face, answer = gx.random_soup(rng)
            assert title and face and answer

    def test_pools_not_empty(self):
        assert len(gx.RIDDLES) >= 10
        assert len(gx.TURTLE_SOUPS) >= 4


# ---------------------------------------------------------------- 抢答


class TestRush:
    def test_first_winner_only(self):
        game = gx.RushGame(keyword="冲", reward=10)
        ok, msg = game.check("冲鸭", "u1", 30)
        assert ok and game.winner == "u1"
        ok2, _ = game.check("冲鸭", "u2", 30)
        assert not ok2

    def test_keyword_must_match(self):
        game = gx.RushGame(keyword="目标")
        ok, _ = game.check("无关内容", "u1", 30)
        assert not ok

    def test_timeout(self):
        game = gx.RushGame(keyword="x")
        assert not game.expired(30, now=game.started_at + 10)
        assert game.expired(30, now=game.started_at + 31)


# ---------------------------------------------------------------- 运势


class TestFortune:
    def test_deterministic_same_day(self):
        a = gx.daily_fortune("s1", "2024-06-01")
        b = gx.daily_fortune("s1", "2024-06-01")
        assert a == b

    def test_varies_by_day_and_seed(self):
        a = gx.daily_fortune("s1", "2024-06-01")
        b = gx.daily_fortune("s1", "2024-06-02")
        c = gx.daily_fortune("s2", "2024-06-01")
        assert a != b and a != c

    def test_fields_in_range(self):
        for i in range(50):
            r = gx.daily_fortune(f"seed{i}")
            assert 1 <= r["luck"] <= 99
            assert 1 <= r["score"] <= 5
            assert len(r["aspects"]) == 3
            assert r["tip"]

    def test_zodiac_cycle(self):
        from datetime import date

        assert gx.zodiac_of(date(2024, 1, 1)) == "龙"
        assert gx.zodiac_of(date(2023, 1, 1)) == "兔"
        assert gx.zodiac_of(date(2000, 1, 1)) == "龙"


# ---------------------------------------------------------------- 商店


class TestShop:
    def test_resolve_by_full_name(self):
        got = gx.resolve_shop_item("称号·大佬")
        assert got is not None and got[0] == "title_dalao"

    def test_resolve_by_suffix(self):
        got = gx.resolve_shop_item("大佬")
        assert got is not None and got[1] == "称号·大佬"

    def test_resolve_by_id(self):
        got = gx.resolve_shop_item("frame_sakura")
        assert got is not None and got[3] == "frame"

    def test_resolve_missing(self):
        assert gx.resolve_shop_item("不存在的道具") is None
        assert gx.resolve_shop_item("") is None

    def test_item_display_name(self):
        assert gx.item_display_name("title_ouhuang") == "称号·欧皇"
        assert gx.item_display_name("unknown_id") == "unknown_id"

    def test_shop_items_well_formed(self):
        for item_id, (name, price, kind, desc) in gx.SHOP_ITEMS.items():
            assert item_id and name and desc
            assert price >= 0
            assert kind in {"title", "frame", "consumable"}


# ---------------------------------------------------------------- 亲密度


class TestIntimacy:
    def test_undirected_key(self):
        assert gx.intimacy_key("a", "b") == gx.intimacy_key("b", "a")

    def test_add_and_get(self):
        bucket: dict = {}
        points, stage = gx.add_intimacy(bucket, "a", "b", 100)
        assert points == 100
        assert stage == "熟人"
        assert gx.get_intimacy(bucket, "b", "a") == 100

    def test_clamped(self):
        bucket: dict = {}
        points, _ = gx.add_intimacy(bucket, "a", "b", -50)
        assert points == 0
        points, _ = gx.add_intimacy(bucket, "a", "b", 10**9)
        assert points == 99999

    def test_relation_stages(self):
        assert gx.relation_stage(0) == "陌生人"
        assert gx.relation_stage(99999) == "灵魂伴侣"
        assert gx.relation_stage(150) == "朋友"


# ---------------------------------------------------------------- 表白 / 对决


class TestSocial:
    def test_confess_line_contains_both(self):
        for _ in range(20):
            line = gx.confess_line("甲", "乙", random.Random(0))
            assert "甲" in line and "乙" in line

    def test_duel_power_non_negative(self):
        rng = random.Random(0)
        for _ in range(100):
            assert gx.duel_power({}, rng) >= 0

    def test_duel_returns_valid(self):
        rng = random.Random(0)
        for _ in range(200):
            winner, diff, flavor = gx.duel({"balance": 100}, {"balance": 100}, rng)
            assert winner in {"a", "b", "draw"}
            assert diff >= 0
            assert flavor

    def test_duel_handles_dirty_data(self):
        rng = random.Random(0)
        winner, diff, _ = gx.duel({"balance": "abc"}, {"balance": None}, rng)
        assert winner in {"a", "b", "draw"}


# ---------------------------------------------------------------- 笑话


class TestJokes:
    def test_random_joke(self):
        rng = random.Random(0)
        for _ in range(20):
            assert gx.random_joke(rng)

    def test_pool_not_empty(self):
        assert len(gx.JOKES) >= 8

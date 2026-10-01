"""games.py 纯逻辑层单元测试。

覆盖签到、抽奖、猜数字、接龙、掷骰、打劫、每日任务等核心算法，
重点回归历史上出现过的边界问题。
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import games  # noqa: E402

# --------------------------------------------------------------------- 签到


def test_sign_in_first_time():
    user = {}
    ok, reward, streak, _ = games.sign_in(user, 10, 10, 0.1, 2.0)
    assert ok and reward == 10 and streak == 1
    assert user["total_sign"] == 1 and user["balance"] == 10


def test_sign_in_twice_same_day_blocked():
    user = {}
    games.sign_in(user, 10, 10, 0.1, 2.0)
    ok, reward, _, msg = games.sign_in(user, 10, 10, 0.1, 2.0)
    assert not ok and reward == 0 and "已经签到" in msg


def test_sign_in_streak_grows_and_is_capped():
    user = {}
    for offset in range(5, 0, -1):
        user["sign_date"] = (date.today() - timedelta(days=offset)).isoformat()
        user["streak"] = 5 - offset
        _, reward, streak, _ = games.sign_in(user, 10, 10, 0.5, 2.0)
    assert streak == 5
    # 1 + 0.5 * 5 = 3.5，但上限是 2.0
    assert reward == 20


def test_sign_in_streak_resets_after_gap():
    user = {"sign_date": "2000-01-01", "streak": 99}
    _, _, streak, _ = games.sign_in(user, 10, 10, 0.1, 2.0)
    assert streak == 1


def test_sign_in_handles_reversed_range():
    user = {}
    ok, reward, _, _ = games.sign_in(user, 50, 10, 0.0, 1.0)
    assert ok and 10 <= reward <= 50


# --------------------------------------------------------------------- 抽奖


def test_lottery_balance_math_is_consistent():
    """余额变化必须与返回的净变化量一致（历史 bug：账目对不上）。"""
    rng = random.Random(7)
    for _ in range(200):
        user = {"balance": 100}
        ok, delta, _, err = games.lottery_draw(user, 20, 0, rng=rng)
        assert ok and not err
        assert user["balance"] == 100 + delta, (user["balance"], delta)


def test_lottery_item_prize_records_title():
    rng = random.Random(3)
    user = {"balance": 1000}
    for _ in range(400):
        ok, _, prize, _ = games.lottery_draw(user, 20, 0, rng=rng)
        assert ok
        if "称号" in prize or "头像框" in prize:
            assert user["title"] == prize.replace(" 🎉", "")
            return
    pytest.fail("400 次抽奖都没抽到 item 奖，权重可能有问题")


def test_lottery_cooldown_blocks():
    user = {"balance": 100, "last_lottery_ts": 1000.0}
    ok, _, _, err = games.lottery_draw(user, 20, 30, now=1010.0)
    assert not ok and "冷却" in err


def test_lottery_insufficient_balance():
    user = {"balance": 5}
    ok, _, _, err = games.lottery_draw(user, 20, 0)
    assert not ok and "积分不足" in err


def test_lottery_never_goes_negative():
    rng = random.Random(11)
    user = {"balance": 0}
    ok, _, _, err = games.lottery_draw(user, 0, 0, rng=rng)
    assert ok and user["balance"] >= 0


# --------------------------------------------------------------------- 猜数字


def test_guess_win_and_lose():
    game = games.GuessGame.new(1, 100, 3, rng=random.Random(1))
    game.target = 50
    assert game.guess(50)[0] == "win"

    game = games.GuessGame.new(1, 100, 2, rng=random.Random(1))
    game.target = 50
    assert game.guess(10)[0] == "low"
    assert game.guess(20)[0] == "lose"


def test_guess_hint_narrows_range():
    game = games.GuessGame.new(1, 100, 10, rng=random.Random(1))
    game.target = 50
    _, hint = game.guess(30)
    assert (game.low, game.high) == (31, 100)
    assert "31" in hint and "100" in hint


def test_guess_close_hint_still_works_after_narrowing():
    """回归：接近提示在区间收窄后曾判定失效。"""
    game = games.GuessGame.new(1, 100, 10, rng=random.Random(1))
    game.target = 50
    hints = []
    for value in (95, 60, 53, 51, 49):
        _, hint = game.guess(value)
        hints.append(hint)
    assert any(any(flavor in h for flavor in games.GUESS_HOT_HINTS) for h in hints)


def test_guess_normalizes_inverted_bounds():
    game = games.GuessGame.new(100, 1, 10)
    assert game.low == 1 and game.high == 100
    assert 1 <= game.target <= 100


# --------------------------------------------------------------------- 接龙


def test_chain_accepts_valid_word():
    game = games.ChainGame(last_word="互动")
    ok, msg = game.submit("动作", "u1")
    assert ok and game.last_word == "动作" and "作" in msg


def test_chain_rejects_wrong_head():
    game = games.ChainGame(last_word="互动")
    ok, msg = game.submit("天气", "u1")
    assert not ok and "动" in msg


def test_chain_rejects_same_user_consecutively():
    game = games.ChainGame(last_word="互动")
    game.submit("动作", "u1")
    ok, msg = game.submit("作业", "u1")
    assert not ok and "同一个人" in msg


def test_chain_rejects_reuse_and_non_chinese():
    game = games.ChainGame(last_word="互动")
    game.submit("动作", "u1")
    assert not game.submit("动作", "u2")[0]
    assert not game.submit("abc", "u2")[0]
    assert not game.submit("动", "u2")[0]
    assert not game.submit("动" * 20, "u2")[0]


def test_chain_tracks_scores():
    game = games.ChainGame(last_word="互动")
    game.submit("动作", "u1")
    game.submit("作业", "u2")
    assert game.scores == {"u1": 1, "u2": 1} and game.round == 2


# --------------------------------------------------------------------- 掷骰 / 打劫


def test_roll_dice_range_and_clamp():
    rolls, total = games.roll_dice(3, 6, rng=random.Random(5))
    assert len(rolls) == 3 and all(1 <= r <= 6 for r in rolls) and total == sum(rolls)


def test_roll_dice_clamps_extreme_input():
    rolls, _ = games.roll_dice(9999, 999999, rng=random.Random(5))
    assert len(rolls) == 10


def test_rob_conserves_or_penalizes():
    rng = random.Random(2)
    attacker = {"balance": 100}
    victim = {"balance": 100}
    ok, delta, _ = games.rob_check(attacker, victim, 10, rng=rng)
    if ok:
        assert delta == 10 and victim["balance"] == 90 and attacker["balance"] == 110
    else:
        assert delta < 0 and attacker["balance"] < 100


def test_rob_reports_insufficient_amount():
    """给对方余额不足时明确拒绝。"""
    ok, delta, msg = games.rob_check({"balance": 100}, {"balance": 1}, 50)
    assert not ok and delta == 0 and "不值得出手" in msg
    assert not games.rob_check({"balance": 10}, {"balance": 100}, 0)[0]


def test_rob_amount_clamped_to_safe_cap():
    """打劫金额会被压到 余额 / ROB_PENALTY_RATE 以内（反刷分核心）。"""
    assert games.rob_max_amount(6, 1000) == 1
    assert games.rob_max_amount(60, 1000) == 10
    assert games.rob_max_amount(600, 1000) == 100
    # 受害者余额更少时以受害者余额为上限
    assert games.rob_max_amount(6000, 100) == 100
    # 一穷二白打不了劫
    assert games.rob_max_amount(5, 1000) == 0
    ok, _, msg = games.rob_check({"balance": 5}, {"balance": 1000}, 50)
    assert not ok and "先攒够" in msg


def test_rob_expected_value_never_positive():
    """期望收益恒不为正——打劫只能转移资产，不能凭空造分。"""
    for attacker_balance in (0, 1, 6, 60, 600, 6000, 60000):
        for victim_balance in (1, 10, 100, 1000, 10000):
            for amount in (1, 10, 100, 1000, 100000):
                cap = games.rob_max_amount(attacker_balance, victim_balance)
                if cap <= 0:
                    continue
                ev = games.rob_expected_value(
                    min(amount, cap), victim_balance, attacker_balance
                )
                assert ev <= 0.01, (attacker_balance, victim_balance, amount, ev)


def test_rob_never_creates_currency_long_run():
    """重复打劫不会让打劫者资产增长（历史漏洞：可无限刷分）。"""
    rng = random.Random(99)
    attacker = {"balance": 600}
    for _ in range(5000):
        victim = {"balance": 100000}
        games.rob_check(attacker, victim, 100000, rng=rng)
    assert attacker["balance"] <= 600


def test_rob_success_rate_bounds():
    assert games.rob_success_rate(1, 1000) <= 0.85
    assert games.rob_success_rate(1000, 1000) >= 0.25
    assert games.rob_success_rate(0, 100) == 0.0
    assert games.rob_success_rate(10, 0) == 0.0


# --------------------------------------------------------------------- 每日任务


def test_quest_progress_and_claim():
    user = {}
    games.bump_daily(user, "chain")
    games.bump_daily(user, "chain")
    games.bump_daily(user, "chain")
    ok, reward, _ = games.claim_quest(user, "chain")
    expected = next(q[3] for q in games.DAILY_QUESTS if q[0] == "chain")
    assert ok and reward == expected and user["balance"] == expected


def test_quest_claim_blocked_when_incomplete():
    user = {}
    games.bump_daily(user, "chain")
    ok, _, msg = games.claim_quest(user, "chain")
    assert not ok and "还差" in msg


def test_quest_claim_twice_blocked():
    user = {}
    for _ in range(3):
        games.bump_daily(user, "chain")
    assert games.claim_quest(user, "chain")[0]
    ok, _, msg = games.claim_quest(user, "chain")
    assert not ok and "已经领过" in msg


def test_quest_unknown_code():
    ok, _, msg = games.claim_quest({}, "nope")
    assert not ok and "没有这个任务" in msg


# --------------------------------------------------------------------- 等级


def test_level_grows_with_balance():
    assert games.level_of({"balance": 0})[0] == 1
    assert games.level_of({"balance": 100})[0] == 2
    assert games.level_of({"balance": 10**9})[0] == 100


def test_quest_progress_resets_across_days():
    """跨天时当日进度必须归零，且不会误清当天已产生的进度。"""
    user = {}
    for _ in range(3):
        games.bump_daily(user, "chain")
    # 第一次读取会补上 quest_date，但绝不能清掉已有的当天进度
    assert [(q["code"], c) for q, c, _ in games.quest_progress(user)][3][1] == 3
    # 明确换成昨天 → 清零
    user["quest_date"] = "2000-01-01"
    assert games.sync_quest_acc(user) is True
    assert user["daily_chain"] == 0
    assert user["quest_done"] == []
    # 同日再调用不会重置
    assert games.sync_quest_acc(user) is False


def test_quest_progress_survives_legacy_archive():
    """只有累计字段的老档案（老版本没有 bump_daily）不应被判成「没做过」。"""
    user = {"total_sign": 12, "quest_date": None}
    progress = {q["code"]: c for q, c, _ in games.quest_progress(user)}
    assert progress["sign"] == 1
    ok, _, _ = games.claim_quest(user, "sign")
    assert ok


def test_level_of_returns_total_and_bar():
    level, inner, need, total = games.level_of({"balance": 250, "total_sign": 10})
    assert (level, inner, need, total) == (4, 0, 100, 300)
    assert games.level_of({"balance": -100})[0] == 1


def test_level_of_tolerates_dirty_values():
    assert games.level_of({"balance": "abc", "total_sign": None})[0] == 1


# --------------------------------------------------------------------- 新增玩法


def test_lucky_number_is_stable_per_day_and_user():
    a = games.lucky_number("2026-10-01", "1001")
    b = games.lucky_number("2026-10-01", "1001")
    assert a == b and games.LUCKY_MIN <= a <= games.LUCKY_MAX
    # 换天 / 换人都会变（至少在足够多的样本里不全相等）
    values = {games.lucky_number(f"2026-10-{d:02d}", "1001") for d in range(1, 29)}
    assert len(values) > 3
    users = {games.lucky_number("2026-10-01", str(i)) for i in range(50)}
    assert len(users) > 5


def test_lucky_hit():
    target = games.lucky_number("2026-10-01", "7")
    assert games.lucky_hit("2026-10-01", "7", target)
    assert not games.lucky_hit("2026-10-01", "7", target + 1)
    assert not games.lucky_hit("2026-10-01", "7", "abc")


def test_eight_ball_requires_question():
    ok, msg = games.eight_ball("")
    assert not ok and "用法" in msg
    ok, msg = games.eight_ball("今天要加班吗")
    assert ok and msg.startswith("🔮") and "今天要加班吗" in msg


def test_eight_ball_truncates_long_question():
    ok, msg = games.eight_ball("问" * 500)
    assert ok and len(msg) < 200


def test_roast_includes_target():
    assert games.roast("小明").startswith("@小明 ")
    assert games.roast("").startswith("@")


def test_dice_faces_text_uses_emoji_for_d6():
    assert games.dice_faces_text([1, 6], 6) == "⚀ ⚅"
    assert games.dice_faces_text([14, 5], 20) == "14 + 5"


def test_format_duration():
    assert games.format_duration(0) == "0 秒"
    assert games.format_duration(90) == "1 分钟"
    assert games.format_duration(7200).startswith("2 小时")
    assert games.format_duration(90061).startswith("1 天 1 小时")


def test_bar_edges():
    assert games.bar(0, 100, 10) == "▱" * 10
    assert games.bar(100, 100, 10) == "▰" * 10
    assert games.bar(50, 0, 4) == "▱▱▱▱"
    assert games.bar(999, 100, 4) == "▰▰▰▰"


def test_percentile_of():
    assert games.percentile_of([], 1) == 0.0
    assert games.percentile_of([1, 2, 3, 4], 3) == 50.0
    assert games.percentile_of([1, 2], 99) == 100.0


def test_normalize_range_handles_dirty_input():
    assert games.normalize_range(10, 1) == (1, 10)
    assert games.normalize_range("abc", None) == (0, 0)
    assert games.normalize_range(5, 5, min_span=1) == (5, 6)
    assert games.normalize_range(1, 100) == (1, 100)


def test_dice_count_face_clamping():
    rolls, total = games.roll_dice(999, 999999, rng=random.Random(1))
    assert len(rolls) == 10 and all(1 <= r <= 1000 for r in rolls)
    assert total == sum(rolls)
    rolls, _ = games.roll_dice(0, 0)
    assert len(rolls) == 1


def test_guess_game_clamps_range():
    game = games.GuessGame.new("abc", "def", -5, rng=random.Random(1))
    assert game.low == 0 and game.high == 1 and game.max_attempts == 1


def test_guess_hint_still_close_after_narrowing():
    game = games.GuessGame.new(1, 100, 10, rng=random.Random(1))
    game.target = 50
    _, hint = game.guess(48, "u1")
    assert "太小了" in hint
    assert "接近" in hint or "一点点" in hint or "感觉" in hint
    assert "u1" in game.players


def test_guess_win_records_winner():
    game = games.GuessGame.new(1, 10, 5, rng=random.Random(1))
    game.target = 5
    result, _ = game.guess(5, "u9")
    assert result == "win" and game.winner == "u9"

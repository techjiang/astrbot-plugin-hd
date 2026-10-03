"""第三批扩展玩法（``games_world``）的单测。

重点是**经济守恒**：Boss 战、赛季、宠物三条线都不能成为印钞机。
这些断言不是「跑通就行」，而是逐条锁死不变量 —— 前两批扩展里
「竞猜虚增选项池」「转盘期望为正」都是靠这类脚本才发现的。
"""

from __future__ import annotations

import random

import pytest
from astrbot_plugin_hudong import games_world as gw


class TestBossDamage:
    def test_damage_equals_spend_over_cost(self):
        assert gw.boss_damage(100, 1) == 100
        assert gw.boss_damage(100, 5) == 20

    def test_cost_is_at_least_one(self):
        """cost 传 0 或负数不能让玩家「零成本无限伤害」。"""
        assert gw.boss_damage(100, 0) == 100
        assert gw.boss_damage(100, -3) == 100

    def test_non_positive_spend_gives_zero(self):
        assert gw.boss_damage(0) == 0
        assert gw.boss_damage(-50) == 0

    def test_dirty_input_does_not_crash(self):
        assert gw.boss_damage("一堆乱码") == 0
        assert gw.boss_damage(None) == 0


class TestBossShare:
    def test_everything_distributed(self):
        """分配额合计必须**严格等于**奖池 —— 取整不能凭空多出或少掉积分。"""
        share = gw.boss_share({"a": 300, "b": 700}, 700)
        assert sum(share.values()) == 700

    def test_remainder_goes_to_top_damage(self):
        share = gw.boss_share({"a": 1, "b": 1, "c": 1}, 100)
        assert sum(share.values()) == 100
        # 余数给伤害最高者；三者伤害相同，按 uid 稳定选出第一个
        assert max(share.values()) >= 34

    def test_zero_total_damage(self):
        assert gw.boss_share({"a": 0}, 100) == {}
        assert gw.boss_share({}, 100) == {}

    def test_zero_pool(self):
        assert gw.boss_share({"a": 10}, 0) == {}

    def test_single_player_gets_whole_pool(self):
        share = gw.boss_share({"solo": 500}, 300)
        assert share == {"solo": 300}


class TestBossFight:
    def test_create_uses_template(self):
        fight = gw.BossFight.create("dragon", 100)
        assert fight.name == "烈焰巨龙"
        assert fight.hp == fight.hp_max
        assert fight.started_at == 100

    def test_unknown_code_falls_back_to_first(self):
        fight = gw.BossFight.create("没有这个boss")
        assert fight.code == gw.BOSSES[0][0]

    def test_attack_reduces_hp(self):
        fight = gw.BossFight.create("slime")
        dealt, remaining, killed = fight.attack("a", 300)
        assert dealt == 300
        assert remaining == fight.hp_max - 300
        assert killed is False

    def test_overkill_is_capped_at_remaining_hp(self):
        """伤害超过剩余血量时，实际伤害要收敛到剩余血量，不能溢出。"""
        fight = gw.BossFight.create("slime")
        dealt, remaining, killed = fight.attack("a", 10**9)
        assert dealt == fight.hp_max
        assert remaining == 0
        assert killed is True

    def test_attack_after_finish_is_noop(self):
        fight = gw.BossFight.create("slime")
        fight.attack("a", 10**9)
        assert fight.finished
        assert fight.attack("b", 100) == (0, 0, False)

    def test_zero_damage_attack_is_noop(self):
        fight = gw.BossFight.create("slime")
        assert fight.attack("a", 0) == (0, fight.hp_max, False)
        assert fight.damages == {}


class TestBossEconomy:
    """Boss 战最重要的不变量：**总产出不超过总投入**。

    奖池刻意小于模板血量（因为 1 积分 = 1 伤害），所以即使全员满额输出，
    玩家的总积分也只会减少。这条断言防止有人日后「顺手」把奖池调大。
    """

    @pytest.mark.parametrize("code", [c for c, *_rest in gw.BOSSES])
    def test_pool_is_less_than_hp(self, code):
        _name, hp, cost, pool, _desc = gw.BOSS_MAP[code]
        total_spent = hp * cost
        assert pool < total_spent, f"{code}: 奖池 {pool} 不应超过所需投入 {total_spent}"

    def test_full_run_is_net_negative(self):
        """全员「打满」后，玩家总积分必然减少（合作有损耗）。"""
        fight = gw.BossFight.create("dragon")
        spent = 0
        for uid in ("a", "b", "c"):
            chunk = 2000
            spent += chunk
            fight.attack(uid, chunk)
        if not fight.finished:
            spent += fight.hp
            fight.attack("d", fight.hp)
        share, _killer = fight.settle()
        assert sum(share.values()) <= spent

    def test_settle_before_finish_returns_empty(self):
        fight = gw.BossFight.create("slime")
        fight.attack("a", 10)
        assert fight.settle() == ({}, "")

    def test_monte_carlo_stays_net_negative(self):
        """随机跑 200 局，逐局核对「总产出 ≤ 总投入」。"""
        rng = random.Random(20261003)
        for _ in range(200):
            code = rng.choice([c for c, *_rest in gw.BOSSES])
            fight = gw.BossFight.create(code)
            spent = 0
            while not fight.finished:
                uid = f"u{rng.randint(0, 4)}"
                chunk = rng.randint(1, 900)
                spent += chunk
                fight.attack(uid, chunk)
            share, _killer = fight.settle()
            assert sum(share.values()) <= spent

    def test_progress_is_percentage(self):
        fight = gw.BossFight.create("slime")
        assert fight.progress == 0
        fight.attack("a", fight.hp_max // 2)
        assert 45 <= fight.progress <= 55
        fight.attack("b", fight.hp_max)
        assert fight.progress == 100

    def test_render_contains_name_and_numbers(self):
        fight = gw.BossFight.create("slime")
        text = fight.render()
        assert "史莱姆王" in text
        assert str(fight.hp_max) in text


class TestSeason:
    def test_season_of_parses_month(self):
        assert gw.season_of("2026-03-15") == "2026-03"
        assert gw.season_of("2026-12-01") == "2026-12"

    def test_season_of_normalizes_month(self):
        assert gw.season_of("2026-3-1") == "2026-03"

    @pytest.mark.parametrize("bad", ["", "乱写", "2026", "-", None])
    def test_season_of_survives_bad_input(self, bad):
        assert gw.season_of(bad) == "未知赛季"

    def test_tier_is_monotonic(self):
        """积分越高，段位奖励不能反而变少。"""
        rewards = [gw.season_reward(v) for v in (0, 600, 3000, 10000, 50000, 200000)]
        assert rewards == sorted(rewards)

    def test_zero_gain_still_gets_base_tier(self):
        assert gw.season_reward(0) == gw.SEASON_TIERS[0][2]

    def test_negative_gain_treated_as_zero(self):
        assert gw.season_reward(-9999) == gw.season_reward(0)

    def test_progress_points_to_next_tier(self):
        stage, next_stage, remain = gw.season_progress(600)
        assert stage == "白银"
        assert next_stage == "黄金"
        assert remain == 2000 - 600

    def test_progress_at_cap(self):
        stage, next_stage, remain = gw.season_progress(10**9)
        assert stage == "王者"
        assert next_stage == "已封顶"
        assert remain == 0


class TestPet:
    def test_stage_progression(self):
        assert gw.pet_stage(0)[0] == "egg"
        assert gw.pet_stage(5)[0] == "baby"
        assert gw.pet_stage(20)[0] == "child"
        assert gw.pet_stage(60)[0] == "adult"
        assert gw.pet_stage(150)[0] == "elder"

    def test_stage_is_monotonic_in_bonus(self):
        bonuses = [gw.pet_stage(v)[2] for v in (0, 5, 20, 60, 150)]
        assert bonuses == sorted(bonuses)

    def test_need_points_to_next_stage(self):
        name, remain = gw.pet_need(0)
        assert (name, remain) == ("幼体", 5)
        assert gw.pet_need(10**9) == ("已封顶", 0)

    def test_gain_never_exceeds_cap(self):
        """产出上限必须锁死 —— 否则养成会变成印钞机。"""
        rng = random.Random(7)
        for care in range(0, 400):
            for _ in range(5):
                assert 0 <= gw.pet_gain(care, rng) <= gw.PET_MAX_GAIN

    def test_gain_is_always_positive_after_hatch(self):
        rng = random.Random(11)
        assert all(gw.pet_gain(0, rng) >= 1 for _ in range(50))

    def test_pet_economy_is_below_a_single_signin(self):
        """单次照料的期望产出，必须低于签到奖励下限（默认 10）。

        这不是「凑巧」而是一条设计约束：养成只提供情绪价值，
        不能成为比签到更划算的刷分路径。
        """
        rng = random.Random(3)
        worst_stage_avg = sum(gw.pet_gain(150, rng) for _ in range(2000)) / 2000
        assert worst_stage_avg < 10, f"最高阶段期望 {worst_stage_avg} 不应接近签到下限"

    def test_state_roundtrip(self):
        state = gw.PetState(name="小互动", care=7, fed_at=100, born=50)
        assert gw.PetState.from_dict(state.to_dict()) == state

    @pytest.mark.parametrize("bad", [None, {}, "乱写", 123, []])
    def test_state_survives_dirty_data(self, bad):
        state = gw.PetState.from_dict(bad)
        assert state.care == 0
        assert state.fed_at == 0

    def test_state_truncates_long_name(self):
        state = gw.PetState.from_dict({"name": "超" * 100})
        assert len(state.name) <= 24

    def test_next_feed_at_respects_cooldown(self):
        state = gw.PetState(fed_at=1000)
        assert state.next_feed_at(1000) == 1000 + gw.PET_FEED_COOLDOWN
        # 已经过了冷却：立即可以喂
        assert (
            state.next_feed_at(1000 + gw.PET_FEED_COOLDOWN + 1)
            == 1000 + gw.PET_FEED_COOLDOWN + 1
        )

    def test_describe_mentions_stage(self):
        text = gw.PetState(name="小互动", care=60).describe()
        assert "成年" in text
        assert "小互动" in text

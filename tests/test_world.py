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
        dealt = min(30, fight.hp)
        dealt, remaining, killed = fight.attack("a", dealt)
        assert dealt == 30
        assert remaining == fight.hp_max - 30
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


class TestBossRoster:
    """Boss 阵容：至少三种、码唯一、弱点打法都在打法表里。"""

    def test_has_several_bosses(self):
        assert len(gw.BOSSES) >= 5

    def test_codes_are_unique(self):
        codes = [code for code, *_rest in gw.BOSSES]
        assert len(codes) == len(set(codes))

    def test_every_boss_has_weakness_style(self):
        """弱点必须是玩家真能打出来的写法（中文名或英文码都算）。"""
        styles = {name for name, _code, _counter in gw.BATTLE_STYLES}
        styles |= {code for _name, code, _counter in gw.BATTLE_STYLES}
        for code, name, *_rest in gw.BOSSES:
            assert gw.BOSS_WEAKNESS.get(code) in styles, (
                f"{name} 的弱点打法不在打法表里"
            )

    def test_battle_style_accepts_both_names(self):
        assert gw.battle_style("消耗") == "消耗"
        assert gw.battle_style("poke") == "消耗"
        assert gw.battle_style("破甲") == "破甲"
        assert gw.battle_style("breach") == "破甲"

    def test_hp_and_pool_are_positive_and_growing(self):
        hp = [row[2] for row in gw.BOSSES]
        assert all(v > 0 for v in hp)
        # 血量按难度递增，但不是严格单调：先上「多只可打」，顺序由策划定
        assert hp == sorted(hp)

    def test_aliases_point_to_real_bosses(self):
        codes = {code for code, *_rest in gw.BOSSES}
        for alias, code in gw.BOSS_ALIASES.items():
            assert code in codes, f"别名 {alias} 指向了不存在的 Boss {code}"


class TestBattleMath:
    """战力 → 伤害 的口径：单调、次线性、有下限。"""

    def test_damage_grows_with_power(self):
        rng = random.Random(20261101)
        weak = sum(gw.battle_damage(100, "slime", "", rng)[0] for _ in range(50))
        strong = sum(gw.battle_damage(1000, "slime", "", rng)[0] for _ in range(50))
        assert strong > weak

    def test_growth_is_sublinear(self):
        """10 倍战力不能带来 10 倍伤害 —— 否则会滚雪球。"""
        rng = random.Random(5)
        low = sum(gw.battle_damage(100, "slime", "", rng)[0] for _ in range(200)) / 200
        high = (
            sum(gw.battle_damage(1000, "slime", "", rng)[0] for _ in range(200)) / 200
        )
        assert high < low * 2.5
        assert high > low

    def test_zero_power_still_hits_minimum(self):
        damage, _ = gw.battle_damage(0, "slime", "", random.Random(1))
        assert damage == gw.BATTLE_MIN_HIT

    def test_counter_style_is_better(self):
        rng = random.Random(9)
        weak = gw.BOSS_WEAKNESS["slime"]
        with_counter = gw.battle_damage(1000, "slime", weak, rng)[0]
        rng = random.Random(9)
        neutral = gw.battle_damage(1000, "slime", "", rng)[0]
        assert with_counter > neutral

    def test_dirty_power_does_not_crash(self):
        for bad in (None, "abc", -5, [], {}):
            damage, _mult = gw.battle_damage(bad, "slime", "", random.Random(2))
            assert damage >= gw.BATTLE_MIN_HIT


class TestBossEconomy:
    """Boss 战的唯一产出是赏金，必须被上限死死锁住。

    新模型不扣积分（战力出伤），所以不能再用「产出 ≤ 投入」来约束；
    改为锁三条硬上限：单人 ≤ 奖池×25%、总额 ≤ 奖池×2、无人参战则不发放。
    """

    @pytest.mark.parametrize("code", [c for c, *_rest in gw.BOSSES])
    def test_reward_is_capped(self, code):
        pool = gw.BOSS_MAP[code][3]
        for people in (1, 2, 5, 20, 100):
            damages = {str(i): 10 for i in range(people)}
            per = gw.battle_reward(pool, damages, people)
            assert per * people <= pool * gw.BATTLE_REWARD_TOTAL_MULT
            assert per <= int(pool * gw.BATTLE_REWARD_SHARE)

    def test_no_attendance_pays_nothing(self):
        assert gw.battle_reward(700, {}, 0) == 0
        assert gw.battle_reward(700, {"a": 0}, 1) == 0

    def test_settle_before_finish_returns_empty(self):
        fight = gw.BossFight.create("slime")
        fight.attack("a", 10)
        assert fight.settle() == ({}, "")

    def test_settle_pays_at_most_cap_per_person(self):
        """全员打满后，每人拿到的赏金都不超过奖池上限。"""
        for code, name, hp, _cost, pool, _desc in gw.BOSSES:
            fight = gw.BossFight.create(code)
            for uid in ("a", "b", "c"):
                fight.attack(uid, hp)
            share, killer = fight.settle()
            assert killer in share
            # boss_share 是「总额守恒」：按伤害占比分完整个奖池
            assert sum(share.values()) == fight.pool
            # 真正发到玩家手上的是 battle_reward，它在 boss_share 之上再套上限
            per = gw.battle_reward(pool, fight.damages, fight.attendance())
            cap = int(pool * gw.BATTLE_REWARD_SHARE)
            assert per <= cap, f"{name}: 单人赏金 {per} 超过上限 {cap}"
            assert per * fight.attendance() <= pool * gw.BATTLE_REWARD_TOTAL_MULT

    def test_monte_carlo_rewards_stay_bounded(self):
        """随机跑 200 局，逐局核对赏金上限。"""
        rng = random.Random(20261101)
        for _ in range(200):
            code = rng.choice([c for c, *_rest in gw.BOSSES])
            pool = gw.BOSS_MAP[code][3]
            fight = gw.BossFight.create(code)
            while not fight.finished:
                uid = f"u{rng.randint(0, 4)}"
                fight.attack(uid, rng.randint(1, 120))
            share, _killer = fight.settle()
            people = fight.attendance()
            per_actual_total = sum(share.values())
            assert per_actual_total <= pool
            per = gw.battle_reward(pool, fight.damages, people)
            assert per * people <= pool * gw.BATTLE_REWARD_TOTAL_MULT

    def test_boss_damage_does_not_feed_power(self):
        """Boss 伤害不能再计入战力：伤害由战力算出，喂回去就是正反馈。"""
        from astrbot_plugin_hudong import games_plus

        fields = [field for field, _label, _factor in games_plus.POWER_FACTORS]
        assert "boss_damage" not in fields
        assert "boss_kill" in fields

    def test_killing_a_boss_does_not_inflate_next_damage(self):
        """打一场 Boss 之后，单场伤害不应显著变大（这是本次要修的滚雪球）。"""
        from astrbot_plugin_hudong import games_plus

        rng = random.Random(2026)
        user = {"total_sign": 100, "boss_kill": 0, "balance": 0}
        before = sum(
            gw.battle_damage(games_plus.battle_power(user), "slime", "", rng)[0]
            for _ in range(50)
        )
        for _ in range(20):
            user["boss_kill"] = int(user.get("boss_kill", 0)) + 1
        after = sum(
            gw.battle_damage(games_plus.battle_power(user), "slime", "", rng)[0]
            for _ in range(50)
        )
        # 20 次击杀会把战力顶到 1000，但伤害只能按对数缓慢增长
        assert after < before * 3


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

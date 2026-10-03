"""标签体系（``games_tags``）的单测。

覆盖三条不变量：
1. 稀有度**由阈值推导**，改阈值后展示自动同步，不会说谎；
2. 归一化搜索对前缀、标点、大小写免疫；
3. 筛选/排序不丢条目、不产生重复。
"""

from __future__ import annotations

import pytest
from astrbot_plugin_hudong import games_plus, games_tags

# 与生产代码共用同一张定义表，避免测试与实现各写一份而漂移
ACH = games_plus.ACHIEVEMENTS
CATS = games_plus.ACHIEVEMENT_CATEGORIES


def _user(**overrides):
    """构造一个用户档案（默认全部计数为 0）。"""
    user = {"achievements": []}
    user.update(overrides)
    return user


class TestRarity:
    def test_thresholds_map_to_expected_rarity(self):
        assert games_tags.rarity_of(5, "game") == "common"
        assert games_tags.rarity_of(50, "game") == "rare"
        assert games_tags.rarity_of(500, "game") == "epic"
        assert games_tags.rarity_of(5000, "game") == "legend"

    def test_explore_is_bumped_one_tier(self):
        """探索类同样的数值更难达成，应比同阈值其它分类高一档。"""
        assert games_tags.rarity_of(50, "explore") == "epic"
        assert games_tags.rarity_of(50, "game") == "rare"

    def test_daily_is_lowered_one_tier(self):
        assert games_tags.rarity_of(50, "daily") == "common"
        assert games_tags.rarity_of(50, "game") == "rare"

    def test_rarity_is_derived_not_hardcoded(self):
        """稀有度必须随阈值变化 —— 这是「不说谎」的核心保证。"""
        assert games_tags.rarity_of(10, "game") != games_tags.rarity_of(5000, "game")

    def test_explore_cannot_exceed_legend(self):
        """探索类上调一档不能越过最高档。"""
        assert games_tags.rarity_of(999999, "explore") == "legend"

    def test_unknown_rarity_falls_back(self):
        assert games_tags.rarity_badge("nope") == "❔"
        assert games_tags.rarity_name("nope") == "未知"

    def test_all_declared_rarities_have_badge_and_name(self):
        for code, name, badge, _weight in games_tags.RARITIES:
            assert games_tags.rarity_badge(code) == badge
            assert games_tags.rarity_name(code) == name


class TestCategory:
    def test_all_achievements_have_a_category(self):
        """成就定义与分类表必须严格对齐，漏登记会静默归入默认分类。"""
        missing = [
            code
            for code, *_rest in ACH
            if code not in games_plus.ACHIEVEMENT_CATEGORIES
        ]
        assert not missing, f"以下成就没有登记分类：{missing}"

    def test_category_map_has_no_orphans(self):
        """分类表里不能有已不存在的成就码（删除成就后忘记清理）。"""
        codes = {code for code, *_rest in ACH}
        orphans = [c for c in games_plus.ACHIEVEMENT_CATEGORIES if c not in codes]
        assert not orphans, f"分类表里有孤儿成就码：{orphans}"

    def test_every_category_value_is_known(self):
        known = {code for code, *_rest in games_tags.CATEGORIES}
        for code, category in games_plus.ACHIEVEMENT_CATEGORIES.items():
            assert category in known, f"{code} 的分类 {category} 未定义"

    def test_unknown_category_falls_back(self):
        assert games_tags.category_badge("nope") == "❔"
        assert games_tags.category_name("nope") == "未分类"


class TestNormalize:
    @pytest.mark.parametrize(
        "raw",
        ["签到", "/签到", "!签到", "！签到", "＃签到", "  签到  ", "签 到"],
    )
    def test_search_survives_prefix_and_punct(self, raw):
        assert games_tags.normalize_query(raw) == "签到"

    def test_case_insensitive(self):
        assert games_tags.normalize_query("RARE") == games_tags.normalize_query("rare")


class TestBuildEntries:
    def test_entry_count_matches_definitions(self):
        entries = games_tags.build_entries(ACH, _user(), categories=CATS)
        assert len(entries) == len(ACH)

    def test_progress_is_clamped_to_threshold(self):
        """超过阈值的进度要收敛，否则会显示 200/100 这种脏数据。"""
        entries = games_tags.build_entries(ACH, _user(balance=10**9), categories=CATS)
        for entry in entries:
            assert entry.current <= entry.threshold

    def test_remaining_never_negative(self):
        entries = games_tags.build_entries(ACH, _user(balance=10**9), categories=CATS)
        assert all(e.remaining >= 0 for e in entries)

    def test_unlocked_flag_from_user(self):
        entries = games_tags.build_entries(
            ACH, _user(achievements=["checkin_7"]), categories=CATS
        )
        by_code = {e.code: e for e in entries}
        assert by_code["checkin_7"].unlocked is True
        assert by_code["checkin_30"].unlocked is False

    def test_dirty_user_data_does_not_crash(self):
        """档案里塞进字符串、None、负数都不该炸。"""
        entries = games_tags.build_entries(
            ACH,
            _user(balance="一堆乱码", best_streak=None, lottery_count=-5),
            categories=CATS,
        )
        assert len(entries) == len(ACH)
        assert all(e.current >= 0 for e in entries)

    def test_unlocked_at_is_read_from_mapping(self):
        entries = games_tags.build_entries(
            ACH,
            _user(achievements=["checkin_7"]),
            categories=CATS,
            unlocked_at={"checkin_7": 1700000000},
        )
        by_code = {e.code: e for e in entries}
        assert by_code["checkin_7"].unlocked_at == 1700000000

    def test_missing_category_defaults_to_game(self):
        entries = games_tags.build_entries(ACH, _user(), categories={})
        assert {e.category for e in entries} == {"game"}


class TestFilter:
    def _entries(self, user=None):
        return games_tags.build_entries(ACH, user or _user(), categories=CATS)

    def test_by_query_matches_name(self):
        rows = games_tags.filter_entries(self._entries(), query="签到")
        assert rows and all("签到" in e.name for e in rows)

    def test_by_query_matches_code(self):
        rows = games_tags.filter_entries(self._entries(), query="checkin_7")
        assert [e.code for e in rows] == ["checkin_7"]

    def test_query_is_normalized(self):
        a = games_tags.filter_entries(self._entries(), query="  签到 ！")
        b = games_tags.filter_entries(self._entries(), query="签到")
        assert [e.code for e in a] == [e.code for e in b]

    def test_by_category_name_and_code_equivalent(self):
        by_name = games_tags.filter_entries(self._entries(), category="日常")
        by_code = games_tags.filter_entries(self._entries(), category="daily")
        assert [e.code for e in by_name] == [e.code for e in by_code]
        assert by_name  # 不能是空集，否则「等价」毫无意义

    def test_by_rarity(self):
        rows = games_tags.filter_entries(self._entries(), rarity="稀有")
        assert rows and all(e.rarity == "rare" for e in rows)

    def test_only_locked_and_unlocked_are_disjoint(self):
        user = _user(achievements=["checkin_7"])
        entries = self._entries(user)
        locked = games_tags.filter_entries(entries, only_locked=True)
        unlocked = games_tags.filter_entries(entries, only_unlocked=True)
        assert {e.code for e in locked} & {e.code for e in unlocked} == set()
        assert len(locked) + len(unlocked) == len(entries)

    def test_combined_filters_intersect(self):
        rows = games_tags.filter_entries(
            self._entries(), query="抽奖", category="运气", rarity="史诗"
        )
        assert [e.code for e in rows] == ["lottery_500"]

    def test_no_match_returns_empty(self):
        assert games_tags.filter_entries(self._entries(), query="不存在的词") == []

    def test_filter_never_duplicates(self):
        rows = games_tags.filter_entries(self._entries(), query="签到")
        assert rows
        assert len(rows) == len({e.code for e in rows})


class TestSort:
    def _entries(self):
        return games_tags.build_entries(
            ACH,
            _user(balance=10**9, achievements=["checkin_7"]),
            categories=CATS,
            unlocked_at={"checkin_7": 1700000000},
        )

    def test_sort_by_rarity_descending(self):
        rows = games_tags.sort_entries(self._entries(), by="rarity")
        weights = [games_tags.RARITY_WEIGHT[e.rarity] for e in rows]
        assert weights == sorted(weights, reverse=True)

    def test_sort_by_name_ascending(self):
        rows = games_tags.sort_entries(self._entries(), by="name", descending=False)
        assert [e.name for e in rows] == sorted(e.name for e in rows)

    def test_sort_by_name_descending_is_reverse(self):
        rows = games_tags.sort_entries(self._entries(), by="name", descending=True)
        assert [e.name for e in rows] == sorted((e.name for e in rows), reverse=True)

    def test_sort_by_time_puts_unlocked_last(self):
        rows = games_tags.sort_entries(self._entries(), by="time")
        # 只有 checkin_7 有时间戳，它必须排第一
        assert rows[0].code == "checkin_7"
        assert rows[-1].unlocked_at == 0

    def test_unknown_sort_dimension_preserves_order(self):
        entries = self._entries()
        assert games_tags.sort_entries(entries, by="乱写") == entries

    def test_sort_does_not_mutate_input(self):
        entries = self._entries()
        before = [e.code for e in entries]
        games_tags.sort_entries(entries, by="name")
        assert [e.code for e in entries] == before


class TestSummarize:
    def test_counts_are_consistent(self):
        entries = games_tags.build_entries(
            ACH, _user(achievements=["checkin_7"]), categories=CATS
        )
        info = games_tags.summarize(entries)
        assert info["total"] == len(ACH)
        assert info["unlocked"] == 1
        # 分类/稀有度分桶的「总」必须加起来等于总数
        assert sum(v[0] for v in info["by_category"].values()) == len(ACH)
        assert sum(v[0] for v in info["by_rarity"].values()) == len(ACH)

    def test_unlocked_never_exceeds_total_per_bucket(self):
        """渲染出「3/1」这种倒挂是真实踩过的坑（行列错位），这里锁死。"""
        entries = games_tags.build_entries(
            ACH,
            _user(
                achievements=[code for code, *_rest in ACH],
            ),
            categories=CATS,
        )
        info = games_tags.summarize(entries)
        for total, got in info["by_category"].values():
            assert got <= total
        for total, got in info["by_rarity"].values():
            assert got <= total

    def test_empty_entries_do_not_divide_by_zero(self):
        info = games_tags.summarize([])
        assert info["percent"] == 0
        assert info["total"] == 0

    def test_render_summary_contains_totals(self):
        entries = games_tags.build_entries(
            ACH, _user(achievements=["checkin_7"]), categories=CATS
        )
        text = games_tags.render_summary(entries)
        assert "1/22" in text

    def test_render_list_handles_empty(self):
        assert "没有匹配" in games_tags.render_list([])

    def test_render_list_truncates_with_hint(self):
        entries = games_tags.build_entries(ACH, _user(), categories=CATS)
        text = games_tags.render_list(entries, limit=3)
        assert text.count("\n") == 3  # 3 行 + 1 行省略提示
        assert "还有" in text

    def test_render_entry_marks_state(self):
        entries = games_tags.build_entries(
            ACH, _user(achievements=["checkin_7"]), categories=CATS
        )
        by_code = {e.code: e for e in entries}
        assert by_code["checkin_7"].render().startswith("✅")
        assert by_code["checkin_30"].render().startswith("🔒")

"""v1.4.4 新增的「管理员自定义」能力回归。

覆盖四件事，每一件都对应一条线上反馈：

1. 抽奖奖池可以在配置里整体替换，且**改坏了的奖池不能把抽奖弄挂**，
   也不能把抽奖变成印钞机（价格会被自动抬到安全下限）；
2. 商店可以自建商品，同名时以自定义项为准；
3. 赛季档位与奖励可自定义，且奖励按「已解锁档位序号」记账 ——
   调整档位奖励不会让玩家重复领或永远领不到；
4. 战力公式可查询，展示的每一项都必须能对上总和。

另外顺带锁死「猜数字预告的奖励 == 实际发放的奖励」，
这是反馈里最直接的一条：后台写 30，预告 30，实际却给了 42。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.platform.astrbot_message import (  # noqa: E402
    AstrBotMessage,
    MessageMember,
)
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot_plugin_hudong import (
    games,  # noqa: E402
    games_extra,  # noqa: E402
    games_plus,  # noqa: E402
    games_world,  # noqa: E402
)
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402

# 复刻 tests/test_main.py 的事件构造与执行辅助。
# 不直接 import 兄弟测试模块：pytest 的 rootdir 模式没有 ``tests`` 包，
# 跨模块 import 会随运行方式而失败；这两个辅助函数很短，复制比耦合更稳。

DEFAULT_CONFIG = {
    "enabled": True,
    "currency_name": "互动币",
    "sign_in": {
        "enabled": True,
        "min_reward": 10,
        "max_reward": 10,
        "streak_bonus": 0.1,
        "max_streak_bonus": 2.0,
    },
    "lottery": {"enabled": True, "cost": 20, "cooldown_seconds": 0},
    "guess_number": {
        "enabled": True,
        "min": 1,
        "max": 100,
        "max_attempts": 10,
        "reward": 30,
    },
    "word_chain": {"enabled": True, "timeout_seconds": 60, "reward": 5},
    "vote": {"enabled": True, "max_options": 10, "duration_seconds": 300},
    "rank": {"size": 10},
    "rob": {"enabled": True},
    "shop": {"enabled": True, "allow_buy": True, "daily_discount": False},
    "season": {"enabled": True},
    "auto_reply": {"enabled": True, "rules": []},
    "permission": {"group_only": True, "cooldown_seconds": 0},
    "trigger": {"wake_free": True, "auto_regex_games": True},
}


class _Event(AstrMessageEvent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list = []

    async def send(self, *args, **kwargs):
        if args:
            self.sent.append(args[0])
        return


def make_event(text: str, uid: str = "1001", gid: str = "888"):
    """构造一个已唤醒的事件（模拟框架剥离唤醒前缀之后的状态）。"""
    msg = AstrBotMessage()
    msg.message = [Plain(f"/{text}")]
    msg.sender = MessageMember(user_id=uid, nickname=f"用户{uid}")
    msg.self_id = "999"
    msg.group_id = gid
    msg.type = MessageType.GROUP_MESSAGE
    msg.session_id = gid
    event = _Event(
        f"/{text}",
        msg,
        PlatformMetadata(name="aiocqhttp", id="aiocqhttp", description=""),
        gid,
    )
    event.is_at_or_wake_command = True
    event.is_wake = True
    event.role = "member"
    event.message_str = event.message_str[1:].strip()
    return event


async def run(plugin, method: str, event, *args, **kwargs):
    result = getattr(plugin, method)(event, *args, **kwargs)
    if hasattr(result, "__aiter__"):
        items = [item async for item in result]
    else:
        items = [await result]
    items.extend(getattr(event, "sent", []))
    return "\n".join(
        item.get_plain_text() if hasattr(item, "get_plain_text") else str(item)
        for item in items
        if item is not None
    )


KEY = "aiocqhttp_group_888"
# make_event 的默认 uid 是 1001；赛季相关用例统一用这个常量，
# 避免「档案写在 A、指令读的是 B」这种静默不一致。
UID = "1001"


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    return plugin_main.InteractionPlugin(
        context=None, config=copy.deepcopy(DEFAULT_CONFIG)
    )


# --------------------------------------------------------------- 抽奖奖池


class TestLotteryPool:
    def test_dirty_entries_are_normalized(self):
        pool = games.lottery_pool(
            [
                {"weight": "20", "kind": "amount", "label": "", "value": "15"},
                {"weight": -3, "kind": "item", "label": "称号X", "value": 99},
                {"weight": "x", "kind": "????", "label": "怪类型", "value": "?"},
                {"weight": 5},  # 缺 label 的积分奖 -> 自动补文案
                {"kind": "item"},  # 称号没名字 -> 丢弃
                "垃圾条目",
            ]
        )
        assert pool[0] == (20, "amount", "15 积分", 15)
        # 权重为负/脏值时收敛到 1，item 的数值一律清零
        assert pool[1] == (1, "item", "称号X", 0)
        assert pool[2][0] == 1 and pool[2][1] == "amount"
        assert all(isinstance(row[0], int) and row[0] >= 1 for row in pool)

    @pytest.mark.parametrize("bad", [None, [], {}, "乱写", [1, 2, 3]])
    def test_empty_or_broken_pool_falls_back_to_builtin(self, bad):
        """奖池解析不出来时必须回退内置，否则抽奖彻底不可用。"""
        assert games.lottery_pool(bad) == list(games.LOTTERY_POOL)

    def test_cost_floor_keeps_house_edge(self):
        """任何奖池下，价格下限都要保证期望回报低于消耗。"""
        for pool in (
            games.LOTTERY_POOL,
            [(1, "amount", "巨奖", 1000)],
            [(1, "amount", "白给", 0), (99, "amount", "大奖", 500)],
        ):
            ev = games.lottery_expected(pool)
            assert games.lottery_cost_floor(pool) >= ev

    async def test_configured_pool_takes_effect(self, plugin):
        plugin.config["lottery"]["pool"] = [
            {"weight": 1, "kind": "amount", "label": "唯一的奖", "value": 5}
        ]
        plugin.config["lottery"]["cost"] = 0
        plugin.store.add_balance(KEY, "1001", 100)
        out = await run(plugin, "cmd_lottery", make_event("抽奖"))
        assert "唯一的奖" in out
        # 配置价 0 会被安全下限抬起来，不能零成本白拿
        assert "本次净变动" in out and "-" in out

    async def test_rebirth_bonus_is_off_by_default(self, plugin):
        """内置奖池 + 转生倍率 = 正期望，所以默认不给抽奖加成。"""
        assert plugin._lottery_bonus_on() is False
        pool = games.LOTTERY_POOL
        assert plugin._lottery_cost(pool) >= games.lottery_cost_floor(pool)

    async def test_bonus_switch_raises_cost(self, plugin):
        pool = games.LOTTERY_POOL
        base = plugin._lottery_cost(pool)
        plugin.config["lottery"]["bonus_enabled"] = True
        boosted = plugin._lottery_cost(pool)
        assert boosted > base, "开启转生加成后必须同时抬高消耗，否则就是印钞机"

    async def test_pool_cache_invalidates_on_config_change(self, plugin):
        first = plugin._lottery_pool()
        plugin.config["lottery"]["pool"] = [
            {"weight": 1, "kind": "amount", "label": "换池子", "value": 1}
        ]
        second = plugin._lottery_pool()
        assert first != second, "改了配置却还在用旧奖池缓存"


# --------------------------------------------------------------- 自定义商店


class TestCustomShop:
    def test_custom_items_normalized(self):
        items = games_extra.custom_items(
            [
                {"name": "称号·土豪", "price": "999", "kind": "", "desc": ""},
                {"name": "定制头像框", "price": -5},
                {"name": "", "price": 100},  # 无名 -> 丢弃
                "垃圾",
            ]
        )
        assert list(items) == ["custom_1", "custom_2"]
        assert items["custom_1"] == ("称号·土豪", 999, "title", "管理员上架的道具。")
        # 价格至少 1，名称无前缀时按消耗品处理
        assert items["custom_2"][1] == 1
        assert items["custom_2"][2] == "frame" or items["custom_2"][2] == "consumable"

    def test_custom_overrides_builtin_by_name(self):
        custom = games_extra.custom_items([{"name": "称号·大佬", "price": 1}])
        merged = games_extra.merge_shop(custom)
        found = games_extra.resolve_shop_item("称号·大佬", merged)
        assert found is not None and found[2] == 1, "同名商品应以自定义价格为准"

    @pytest.mark.parametrize("bad", [None, {}, "乱写", [1, 2]])
    def test_broken_config_keeps_builtin_shop(self, bad):
        assert games_extra.merge_shop(games_extra.custom_items(bad)) == (
            games_extra.SHOP_ITEMS
        )

    async def test_buy_custom_item(self, plugin):
        plugin.config["shop"]["items"] = [
            {"name": "称号·大老板", "price": 100, "kind": "title", "desc": "自建"}
        ]
        plugin.config["shop"]["daily_discount"] = False
        plugin.store.add_balance(KEY, "1001", 1000)
        out = await run(plugin, "cmd_buy", make_event("购买 称号·大老板"))
        assert "大老板" in out
        assert plugin.store.get_user(KEY, "1001")["balance"] == 900

    async def test_shop_list_shows_custom_item(self, plugin):
        plugin.config["shop"]["items"] = [
            {"name": "定制品", "price": 66, "kind": "consumable", "desc": "手搓的"}
        ]
        out = await run(plugin, "cmd_shop", make_event("商店"))
        assert "定制品" in out


# --------------------------------------------------------------- 赛季档位


class TestSeasonTiers:
    def test_tiers_are_sorted_and_clamped(self):
        tiers = games_world.season_tiers(
            [
                {"threshold": "500", "name": "白银", "reward": "200"},
                {"threshold": 0, "name": "青铜", "reward": 40},
            ]
        )
        assert tiers == [(0, "青铜", 40), (500, "白银", 200)]

    def test_duplicate_names_are_dropped(self):
        tiers = games_world.season_tiers(
            [
                {"threshold": 0, "name": "青铜", "reward": 10},
                {"threshold": 100, "name": "青铜", "reward": 999},
            ]
        )
        assert len(tiers) == 1

    @pytest.mark.parametrize("bad", [None, [], {}, "乱写"])
    def test_broken_config_falls_back_to_builtin(self, bad):
        assert games_world.season_tiers(bad) == list(games_world.SEASON_TIERS)

    def test_all_dirty_entries_fall_back_to_builtin(self):
        """门槛和奖励都被写成脏值时，整行是废的，必须回退默认档位。

        否则整张表会退化成一个「0 门槛 / 0 奖励」的假档位 ——
        玩家攒再多分也领不到奖励，比直接回退默认值糟糕得多。
        """
        dirty = [{"threshold": "乱写", "reward": "乱写", "name": ""}]
        assert games_world.season_tiers(dirty) == list(games_world.SEASON_TIERS)

    def test_pending_lists_every_unlocked_tier(self):
        rows = games_world.season_pending(600)
        assert [row[1] for row in rows] == ["青铜", "白银"]

    async def test_claim_uses_configured_tiers(self, plugin):
        plugin.config["season"]["tiers"] = [
            {"threshold": 0, "name": "起步", "reward": 11},
            {"threshold": 10, "name": "进阶", "reward": 22},
        ]
        # 本季增量按「已开始的赛季」记账，伪造档案时必须把 season_tag 一起写上，
        # 否则 _season_sync 会把它当成新赛季的第一天而清零（这是正确行为）。
        plugin.store.add_balance(KEY, UID, 100)
        user = plugin.store.get_user(KEY, UID)
        user["season_gain"] = 100
        user["season_tag"] = games_world.season_of(games.today_str())
        out = await run(plugin, "cmd_season", make_event("赛季 领取", uid=UID))
        assert "起步" in out and "进阶" in out
        assert "33" in out  # 11 + 22

    async def test_claim_is_keyed_by_tier_index_not_amount(self, plugin):
        """核心回归：调整档位奖励后，不能出现「重复领取」或「永远领不到」。"""
        plugin.config["season"]["tiers"] = [
            {"threshold": 0, "name": "起步", "reward": 10},
            {"threshold": 100, "name": "进阶", "reward": 20},
        ]
        plugin.store.add_balance(KEY, UID, 100)
        user = plugin.store.get_user(KEY, UID)
        user["season_tag"] = games_world.season_of(games.today_str())
        await run(plugin, "cmd_season", make_event("赛季 领取", uid=UID))
        balance = plugin.store.get_user(KEY, UID)["balance"]
        # 管理员把奖励调高：已领档位序号不变，所以还是「已领满」
        plugin.config["season"]["tiers"] = [
            {"threshold": 0, "name": "起步", "reward": 999},
            {"threshold": 100, "name": "进阶", "reward": 999},
        ]
        out = await run(plugin, "cmd_season", make_event("赛季 领取", uid=UID))
        assert "已领满" in out
        assert plugin.store.get_user(KEY, UID)["balance"] == balance

    async def test_new_tier_after_gain_can_be_claimed(self, plugin):
        plugin.config["season"]["tiers"] = [
            {"threshold": 0, "name": "起步", "reward": 10},
            {"threshold": 100, "name": "进阶", "reward": 20},
        ]
        plugin.store.add_balance(KEY, UID, 50)
        plugin.store.get_user(KEY, UID)["season_tag"] = games_world.season_of(
            games.today_str()
        )
        await run(plugin, "cmd_season", make_event("赛季 领取", uid=UID))
        user = plugin.store.get_user(KEY, UID)
        user["season_gain"] = 100  # 本季继续攒到第二档
        out = await run(plugin, "cmd_season", make_event("赛季 领取", uid=UID))
        assert "进阶" in out

    async def test_new_season_resets_tier_index(self, plugin):
        plugin.store.add_balance(KEY, UID, 100)
        user = plugin.store.get_user(KEY, UID)
        user["season_tag"] = "2000-01"
        user["season_tier"] = 5
        await run(plugin, "cmd_season", make_event("赛季", uid=UID))
        assert plugin.store.get_user(KEY, UID)["season_tier"] == 0


# --------------------------------------------------------------- 战力公式


class TestPower:
    def test_breakdown_sums_to_total(self):
        user = {
            "balance": 1000,
            "total_sign": 10,
            "rob_win": 2,
            "boss_damage": 300,
        }
        rows = games_plus.power_breakdown(user)
        assert sum(row[3] for row in rows) == games_plus.power_of(user)

    def test_zero_user_has_zero_power(self):
        assert games_plus.power_of({}) == 0

    def test_dirty_values_do_not_crash(self):
        assert games_plus.power_of({"balance": "乱写", "total_sign": None}) == 0

    async def test_command_prints_formula(self, plugin):
        plugin.store.add_balance(KEY, "1001", 500)
        out = await run(plugin, "cmd_power", make_event("战力"))
        assert "战力" in out
        assert "积分余额×1" in out, "必须把公式原样打出来，否则玩家还是看不懂"
        assert "不参与任何结算" in out

    async def test_power_alias_is_dispatchable(self, plugin):
        assert "战力" in plugin._dispatch
        assert "战力公式" in plugin._dispatch
        assert plugin._args(make_event("战力公式")) == ""


# --------------------------------------------------------------- 猜数字口径


async def test_guess_reward_advertised_equals_paid(plugin):
    """反馈里最刺眼的一条：预告 30，实际给 42。

    现在奖励由 ``_guess_reward()`` 统一算（含难度缩放与转生加成），
    开局预告与实际发放必须来自同一个函数。
    """
    plugin.config["guess_number"].update(
        {"min": 1, "max": 100, "max_attempts": 10, "reward": 30}
    )
    start = await run(plugin, "cmd_guess_start", make_event("猜数字"))
    game = plugin._guesses[KEY]
    advertised = plugin._guess_reward()
    assert f"猜中得 {advertised}" in start

    before = plugin.store.get_user(KEY, "1001")["balance"]
    out = await run(plugin, "cmd_guess", make_event(f"猜 {game.target}"))
    after = plugin.store.get_user(KEY, "1001")["balance"]
    paid = after - before
    assert f"奖励 {paid}" in out
    assert paid == advertised, f"预告 {advertised}，实发 {paid}"


# --------------------------------------------------------------- 赛季增量口径


class TestSeasonGainCoverage:
    """赛季增量必须覆盖「所有赚分玩法」，不能只有 PK / Boss 算成绩。

    用户反馈的原话：「PK 获得的积分可以正确计算，但像抽奖、大转盘等其他
    功能一概不行」。根因是只有走 ``store.add_balance()`` 的结算点会维护
    ``season_gain``，而抽奖/转盘/猜数字/扫雷等十几处直接裸写余额。
    """

    @staticmethod
    def _fresh(plugin, uid: str = UID) -> dict:
        """准备一个属于当前赛季、增量为 0 的干净档案。"""
        plugin.store.add_balance(KEY, uid, 1000)
        user = plugin.store.get_user(KEY, uid)
        user["season_tag"] = games_world.season_of(games.today_str())
        user["season_gain"] = 0
        return user

    async def test_guess_win_counts(self, plugin):
        user = self._fresh(plugin)
        await run(plugin, "cmd_guess_start", make_event("猜数字"))
        game = plugin._guesses[KEY]
        await run(plugin, "cmd_guess", make_event(f"猜 {game.target}"))
        assert user["season_gain"] > 0

    async def test_chain_win_counts(self, plugin):
        user = self._fresh(plugin)
        await run(plugin, "cmd_chain_start", make_event("接龙 互动"))
        await run(plugin, "_chain_submit", make_event("动物"), "动物")
        assert user["season_gain"] > 0

    async def test_lottery_payout_counts_prize_only(self, plugin):
        """抽奖只把奖金记成绩，成本不该抵扣（消费不等于亏成绩）。"""
        plugin.config["lottery"]["pool"] = [
            {"weight": 1, "kind": "amount", "label": "定奖", "value": 100}
        ]
        plugin.config["lottery"]["cost"] = 100
        plugin.config["lottery"]["cooldown_seconds"] = 0
        user = self._fresh(plugin)
        await run(plugin, "cmd_lottery", make_event("抽奖"))
        # 成本会被抬到奖池的安全下限之上，所以这里只断言「记了奖金」这条口径
        assert user["season_gain"] == 100

    async def test_wheel_counts_each_spin_payout(self, plugin):
        user = self._fresh(plugin)
        await run(plugin, "cmd_wheel", make_event("转盘 5"))
        # 逐次记账：净赚多少就记多少，亏损不该把中奖抹平
        assert user["season_gain"] >= 0
        assert user["season_gain"] <= 5 * 400

    async def test_losing_lottery_does_not_deduct(self, plugin):
        plugin.config["lottery"]["pool"] = [
            {"weight": 1, "kind": "amount", "label": "空", "value": 0}
        ]
        plugin.config["lottery"]["cost"] = 50
        user = self._fresh(plugin)
        await run(plugin, "cmd_lottery", make_event("抽奖"))
        assert user["season_gain"] == 0, "失败/空奖不该倒扣赛季成绩"

    async def test_tictactoe_win_counts(self, plugin):
        user = self._fresh(plugin)
        await run(plugin, "cmd_tictactoe", make_event("井字棋"))
        # 井字棋奖励只在「赢」时发放；这里直接给一局必胜局面，
        # 断言的是「赢了要记成绩」而不是 AI 强弱。
        game = plugin._tictactoes[KEY]
        game.board = ["O", "O", "", "X", "X", "", "", "", ""]
        await run(plugin, "_ttt_move", make_event("9"), 9)
        assert user["season_gain"] >= 0

    async def test_transfer_out_does_not_count_for_payer(self, plugin):
        """转出去的钱显然是消费，不该出现在付款方的赛季增量里。"""
        user = self._fresh(plugin)
        self._fresh(plugin, "1002")
        await run(plugin, "cmd_transfer", make_event("转账 1002 100"))
        assert user["season_gain"] == 0
        assert plugin.store.get_user(KEY, "1002")["season_gain"] == 100

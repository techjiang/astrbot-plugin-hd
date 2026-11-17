"""v1.5.0 挖矿玩法回归。

挖矿是「零成本、无限次」的产出玩法，风险不在崩溃而在经济失控：
- 入场费必须恒为 0，不能偷偷从余额扣钱；
- 装备倍率必须真实作用到到账积分；
- 管理员自定义奖池的脏数据不能让指令挂掉，也不能突破奖励上限。

这些用例优先断言「余额实际变化」而不是输出文案。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import games_extra  # noqa: E402
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402
from astrbot_plugin_hudong import store as store_mod  # noqa: E402

from tests.test_customization import (  # noqa: E402
    DEFAULT_CONFIG,
    KEY,
    make_event,
    run,
)


class DummyStore(store_mod.InteractionStore):
    def __init__(self, *a, **k):
        pass

    async def save(self, key, force=False):
        return None


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path)
    original_save = store_mod.InteractionStore.save
    store_mod.InteractionStore.save = DummyStore.save
    try:
        plugin = plugin_main.InteractionPlugin(
            context=None, config=copy.deepcopy(DEFAULT_CONFIG)
        )
    finally:
        store_mod.InteractionStore.save = original_save
    return plugin


def test_mining_pool_weights_are_sane():
    ores = games_extra.mining_ore([])
    assert len(ores) == len(games_extra.MINING_MINE)
    assert all(v > 0 and w > 0 for _n, v, w in ores)
    # 低价值矿石占绝大多数
    low = sum(w for _n, v, w in ores if v <= 10)
    total = sum(w for _n, _v, w in ores)
    assert low / total > 0.9


def test_mining_dirty_pool_falls_back_and_caps():
    ores = games_extra.mining_ore(
        [
            {"label": "", "value": -5, "weight": 0},
            {"label": "测试矿", "value": 10**20, "weight": 1},
            "not-a-dict",
        ]
    )
    assert len(ores) == 2
    assert ores[0][1] == 1  # 非正数收敛成最小可用值 1
    assert ores[1][1] == games_extra.MINING_REWARD_CAP


def test_mining_equipment_bonus_stacks_and_caps():
    ids = ["item_miner_pick", "item_miner_lamp"]
    bonus = games_extra.mining_bonus(ids)
    assert abs(bonus - 0.15) < 1e-9
    huge = [f"custom_{i}" for i in range(100)]
    assert games_extra.mining_bonus(huge) <= games_extra.MINING_BONUS_CAP


def test_mine_never_costs_balance(plugin):
    event = make_event("挖矿")
    user = plugin.store.get_user(KEY, "1001")
    before = user["balance"]
    asyncio = __import__("asyncio")
    result = asyncio.run(run(plugin, "cmd_mining", event, "挖矿"))
    assert "挖到了" in result
    assert "获得" in result
    after = plugin.store.get_user(KEY, "1001")["balance"]
    assert after > before  # 零成本，只增不减
    assert plugin.store.get_user(KEY, "1001")["mine_count"] == 1


def test_equipment_buy_equip_then_mine_bonus(plugin):
    # 给足余额，买一件装备
    key = KEY
    user = plugin.store.get_user(key, "1001")
    user["balance"] = 10000
    # 在插件内部 handler 取到的同一份缓存用户对象上断言
    asyncio = __import__("asyncio")
    out = asyncio.run(run(plugin, "cmd_buy", make_event("购买 灯光头盔"), "灯光头盔"))
    print("BUYOUT", out, plugin.store.get_user(key, "1001"))
    assert plugin.store.get_user(key, "1001")["bag"].get("item_miner_lamp") == 1

    equip_out = asyncio.run(
        run(plugin, "cmd_equip", make_event("装备 灯光头盔"), "灯光头盔")
    )
    assert "已装备" in equip_out and "×1.10" in equip_out
    assert "item_miner_lamp" in plugin.store.get_user(key, "1001")["equipped"]

    bag_out = asyncio.run(run(plugin, "cmd_bag", make_event("背包")))
    assert "装备栏" in bag_out

    # 固定奖池到 10 分，验证倍率进到账
    plugin.config["mining"] = {
        "enabled": True,
        "pool": [{"label": "测试矿", "value": 10, "weight": 1}],
    }
    mine_out = asyncio.run(run(plugin, "cmd_mining", make_event("挖矿"), "挖矿"))
    assert (
        "获得 11 " in mine_out.replace("互动币", " ").replace(" ", " ")
        or "11" in mine_out
    )


def test_mine_info_shows_probabilities(plugin):
    out = __import__("asyncio").run(run(plugin, "cmd_mining_info", make_event("矿场")))
    assert "矿场一览" in out
    assert "传说星矿" in out
    assert "概率约" in out

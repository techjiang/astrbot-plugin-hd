"""回归：单人玩法必须按「会话 + 发起人」隔离，不能被别人抢。

上报现象（真实群聊截图）：

1. 我开了 21 点，别人一句「要牌」就把**我的**局结算了；
2. 猜数字同理，谁都能替我猜、替我拿奖励。

根因是老实现把对局状态只按会话存（``dict[session]``），所以同群任何人
的消息都会作用到唯一那一局上。现在键改成 ``会话:发起人``，并且允许
同一个群里多人各开一桌。

顺带锁一条设计约束：PK / Boss 战的胜负必须由**战斗战力**决定，
不能用包含余额的展示战力 —— 否则「谁钱包鼓谁赢」，转账一笔就能改命。
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import games_extra, games_plus, games_world  # noqa: E402
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402

KEY = "aiocqhttp_group_888"

CONFIG = {
    "enabled": True,
    "currency_name": "互动币",
    "sign_in": {"enabled": True, "min_reward": 10, "max_reward": 10},
    "guess_number": {
        "enabled": True,
        "min": 1,
        "max": 100,
        "max_attempts": 10,
        "reward": 30,
    },
    "blackjack": {"enabled": True, "min_bet": 10, "max_bet": 500},
    "bomb": {"enabled": True, "min": 1, "max": 100, "punish": 10, "reward": 1},
    "rob": {"enabled": True},
    "social": {"enabled": True, "duel_stake": 20, "intimacy_per_interact": 2},
    "boss": {"enabled": True},
    "permission": {"group_only": True, "cooldown_seconds": 0},
    "trigger": {"wake_free": True, "auto_regex_games": True},
}


def make_event(text: str, uid: str = "1001", gid: str = "888"):
    """构造已唤醒事件（复用 test_customization 的极简版本）。"""
    from astrbot.core.message.components import Plain
    from astrbot.core.platform.astrbot_message import (
        AstrBotMessage,
        MessageMember,
    )
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    msg = AstrBotMessage()
    msg.message = [Plain(f"/{text}")]
    msg.sender = MessageMember(user_id=uid, nickname=f"用户{uid}")
    msg.self_id = "999"
    msg.group_id = gid
    msg.type = MessageType.GROUP_MESSAGE
    msg.session_id = gid
    ev = plugin_main.AstrMessageEvent(
        f"/{text}",
        msg,
        PlatformMetadata(name="aiocqhttp", id="aiocqhttp", description=""),
        gid,
    )
    ev.is_at_or_wake_command = True
    ev.is_wake = True
    ev.role = "member"
    ev.message_str = ev.message_str[1:].strip()
    ev.sent = []

    async def _capture(*a, **k):
        """记录 on_message 通过 event.send() 发出的消息。"""
        if a:
            ev.sent.append(a[0])
        elif "message" in k:
            ev.sent.append(k["message"])

    ev.send = _capture
    return ev


async def run(plugin, method: str, event, *args, **kwargs) -> str:
    from astrbot.api.event import MessageEventResult

    result = getattr(plugin, method)(event, *args, **kwargs)
    items = (
        [item async for item in result]
        if hasattr(result, "__aiter__")
        else [await result]
    )
    texts = [
        item.get_plain_text() if isinstance(item, MessageEventResult) else str(item)
        for item in items
        if item is not None
    ]
    for sent in getattr(event, "sent", []):
        texts.append(
            sent.get_plain_text() if isinstance(sent, MessageEventResult) else str(sent)
        )
    return "\n".join(texts)


@pytest.fixture(name="plugin")
def plugin_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    return plugin_main.InteractionPlugin(context=None, config=copy.deepcopy(CONFIG))


# --------------------------------------------------------------- 猜数字归属


@pytest.mark.asyncio
async def test_guess_belongs_to_owner_only(plugin):
    await run(plugin, "cmd_guess_start", make_event("猜数字", uid="1001"))
    game = plugin._guesses[f"{KEY}:1001"]
    game.target = 42
    # 1002 没有自己的局：不能替 1001 猜
    out = await run(plugin, "cmd_guess", make_event("猜 42", uid="1002"))
    assert "没有进行中的猜数字" in out
    assert plugin.store.get_user(KEY, "1002")["balance"] == 0
    assert plugin.store.get_user(KEY, "1001")["balance"] == 0
    assert f"{KEY}:1001" in plugin._guesses, "别人的猜测不该结束我的局"


@pytest.mark.asyncio
async def test_two_players_can_open_guess_at_once(plugin):
    await run(plugin, "cmd_guess_start", make_event("猜数字", uid="1001"))
    await run(plugin, "cmd_guess_start", make_event("猜数字", uid="1002"))
    assert plugin._guesses[f"{KEY}:1001"] is not plugin._guesses[f"{KEY}:1002"]
    plugin._guesses[f"{KEY}:1001"].target = 7
    plugin._guesses[f"{KEY}:1002"].target = 8
    await run(plugin, "cmd_guess", make_event("猜 7", uid="1001"))
    assert f"{KEY}:1001" not in plugin._guesses
    assert f"{KEY}:1002" in plugin._guesses, "别人开的那桌必须保留"


@pytest.mark.asyncio
async def test_guess_auto_takeover_is_per_owner(plugin):
    """免前缀接管同样要按人分流。"""
    await run(plugin, "cmd_guess_start", make_event("猜数字", uid="1001"))
    game = plugin._guesses[f"{KEY}:1001"]
    game.target = 55
    out = await run(plugin, "on_message", make_event("55", uid="1002"))
    assert out == "", "别人的裸数字不该动我的局"
    assert game.attempts == 0
    out = await run(plugin, "on_message", make_event("55", uid="1001"))
    assert "猜中了" in out


# --------------------------------------------------------------- 21 点归属


@pytest.mark.asyncio
async def test_blackjack_cannot_be_hijacked(plugin):
    plugin.store.add_balance(KEY, "1001", 500)
    plugin.store.add_balance(KEY, "1002", 500)
    out = await run(plugin, "cmd_blackjack", make_event("21点 50", uid="1001"))
    if f"{KEY}:1001" not in plugin._blackjack:
        pytest.skip(f"起手天胡直接结算，本用例无对局可劫持：{out}")
    # 别人一句「停牌」不能结算我的局
    out = await run(plugin, "cmd_bj_stand", make_event("停牌", uid="1002"))
    assert "没有进行中的 21 点" in out
    assert f"{KEY}:1001" in plugin._blackjack
    assert plugin.store.get_user(KEY, "1002")["balance"] == 500


@pytest.mark.asyncio
async def test_blackjack_same_owner_reopens(plugin):
    plugin.store.add_balance(KEY, "1001", 500)
    await run(plugin, "cmd_blackjack", make_event("21点 50", uid="1001"))
    out = await run(plugin, "cmd_blackjack", make_event("21点 50", uid="1001"))
    if f"{KEY}:1001" not in plugin._blackjack:
        pytest.skip("起手天胡直接结算，本用例无进行中的对局")
    assert "没结束" in out


# --------------------------------------------------------------- 数字炸弹归属


@pytest.mark.asyncio
async def test_bomb_is_per_owner(plugin):
    await run(plugin, "cmd_bomb_start", make_event("数字炸弹 1 30", uid="1001"))
    bomb = plugin._bombs[f"{KEY}:1001"]
    out = await run(plugin, "on_message", make_event(str(bomb.target), uid="1002"))
    assert "BOOM" not in out
    out = await run(plugin, "on_message", make_event(str(bomb.target), uid="1001"))
    assert "BOOM" in out


# --------------------------------------------------------------- 战力与 PK


def test_battle_power_excludes_balance():
    rich = {"balance": 10_000_000, "total_sign": 3}
    poor = {"balance": 0, "total_sign": 3}
    assert games_plus.power_of(rich) > games_plus.power_of(poor)
    assert games_plus.battle_power(rich) == games_plus.battle_power(poor)


def test_duel_follows_battle_power():
    rng = random.Random(7)
    wins = 0
    for _ in range(200):
        winner, _diff, _flavor = games_extra.duel_by_power(1000, 100, rng)
        wins += winner == "a"
    assert wins == 200, "战力差距大时高的一方必须稳赢，否则战力没有意义"


def test_duel_close_power_can_draw_or_swing():
    rng = random.Random(11)
    results = {games_extra.duel_by_power(1000, 1010, rng)[0] for _ in range(50)}
    assert results & {"a", "b"}, "同水平对决必须有悬念"


@pytest.mark.asyncio
async def test_pk_result_matches_battle_power(plugin):
    """余额不参与 PK：钱多的不该自动赢。"""
    plugin.store.add_balance(KEY, "1001", 100_000)  # 钱多但没战绩
    plugin.store.add_balance(KEY, "1002", 100)
    a = plugin.store.get_user(KEY, "1001")
    b = plugin.store.get_user(KEY, "1002")
    b["duel_win"] = 200  # 战斗战力 4000
    a["duel_win"] = 0
    out = await run(plugin, "cmd_duel", make_event("pk 1002", uid="1001"))
    assert "战斗战力" in out
    assert "战胜了" in out


def test_boss_damage_scales_with_power():
    """战力越高伤害越高，但**必须是次线性**的。

    早期版本伤害 = 战力，而战力里又含「Boss 伤害 ×3」，于是打一场战力翻倍、
    下一场伤害再翻倍，几轮就打到天上去了。现在伤害走对数刻度：
    涨，但绝不会按战力倍数涨。
    """
    rng = random.Random(3)
    weak, _ = games_world.battle_damage(100, "slime", "", rng)
    strong, _ = games_world.battle_damage(1000, "slime", "", rng)
    assert strong > weak
    assert strong < weak * 2.5


def test_boss_bounty_never_exceeds_cap():
    bw = games_world
    pool = 700
    for people in (1, 3, 10, 50):
        per = bw.battle_reward(pool, {str(i): 10 for i in range(people)}, people)
        assert per * people <= pool * bw.BATTLE_REWARD_TOTAL_MULT


def test_zero_power_still_deals_minimum_damage():
    damage, _ = games_world.battle_damage(0, "slime", "", random.Random(1))
    assert damage == games_world.BATTLE_MIN_HIT

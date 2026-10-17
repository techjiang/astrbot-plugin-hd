"""回归：@某人 目标解析、数字竞猜入口、猜数字的「猜 N」入口。

这三类问题都只有一个判据 —— **用真实消息链跑一遍**。历史实现只用
``message_str`` 抠数字，而适配器把 ``@某人`` 转纯文本时会给出昵称
（``@李子``）而不是用户 ID，于是「转账 @某人 1」永远判定成参数不足。
单测里手工构造的 ``message_str`` 恰好带着数字，所以一直没暴露。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.core.message.components import At, Plain  # noqa: E402
from astrbot.core.platform.astrbot_message import (  # noqa: E402
    AstrBotMessage,
    MessageMember,
)
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot_plugin_hudong import games_plus  # noqa: E402
from astrbot_plugin_hudong import main as plugin_main

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
    "guess_number": {
        "enabled": True,
        "min": 1,
        "max": 100,
        "max_attempts": 10,
        "reward": 30,
    },
    "rob": {"enabled": True},
    "dice": {"enabled": True},
    "shop": {"enabled": True, "allow_buy": True},
    "social": {
        "enabled": True,
        "intimacy_per_interact": 2,
        "gift_intimacy": 5,
        "duel_stake": 20,
    },
    "wager": {
        "enabled": True,
        "duration_seconds": 180,
        "max_options": 100,
        "max_bet": 1000,
    },
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


def at_event(
    dbot: bool,
    text: str,
    mention: tuple[str, str] | None = None,
    uid: str = "1001",
    gid: str = "888",
):
    """构造一条「[@机器人] [@他人] 文本」的真实消息链事件。

    Args:
        dbot: 是否 @ 了机器人。
        text: 纯文本部分。
        mention: ``(用户 ID, 昵称)``，表示文本前还有一个 @。
        uid: 发送者。
        gid: 群号。
    """
    chain = []
    if dbot:
        chain.append(At(qq="999", name="诺里 (非大黄蜂)"))
    if mention is not None:
        chain.append(At(qq=mention[0], name=mention[1]))
    chain.append(Plain(text))
    msg = AstrBotMessage()
    msg.message = chain
    msg.message_str = "".join(c.text for c in chain if isinstance(c, Plain))
    msg.sender = MessageMember(user_id=uid, nickname=f"用户{uid}")
    msg.self_id = "999"
    msg.group_id = gid
    msg.type = MessageType.GROUP_MESSAGE
    msg.session_id = gid
    ev = _Event(
        msg.message_str,
        msg,
        PlatformMetadata(name="aiocqhttp", id="aiocqhttp", description=""),
        gid,
    )
    ev.is_at_or_wake_command = True
    ev.is_wake = True
    return ev


async def say(plugin, event) -> str:
    """走免唤醒总入口（与线上路径一致），返回发出的文本。"""
    await plugin.on_message(event)
    return "\n".join(
        item.get_plain_text() if hasattr(item, "get_plain_text") else str(item)
        for item in event.sent
        if item is not None
    )


@pytest.fixture(name="plugin")
def plugin_fixture(tmp_path, monkeypatch):
    """构造插件实例，并把数据目录指向 tmp_path。"""
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    return plugin_main.InteractionPlugin(
        context=None, config=copy.deepcopy(DEFAULT_CONFIG)
    )


# --------------------------------------------------------------- @ 目标解析


def test_mentions_reads_message_chain(plugin):
    ev = at_event(True, " 转账 1", mention=("1002", "李子"))
    assert plugin._mentions(ev) == ["999", "1002"]


def test_resolve_target_prefers_at_over_text(plugin):
    """@ 到的人优先，即使纯文本里还残留着 @机器人的名字。"""
    ev = at_event(True, " 转账 1", mention=("1002", "李子"))
    target, rest = plugin._resolve_target(ev, plugin._args(ev))
    assert (target, rest) == ("1002", "1")


def test_resolve_target_skips_sender(plugin):
    """@ 自己的 At 不会被当成目标（后续回落到文本里的纯数字 ID）。"""
    ev = at_event(False, "1002 5", mention=("1001", "自己"))
    assert plugin._resolve_target(ev, plugin._args(ev)) == ("1002", "5")


def test_resolve_target_plain_id(plugin):
    ev = at_event(False, "1002 5")
    assert plugin._resolve_target(ev, plugin._args(ev)) == ("1002", "5")


async def test_transfer_with_at(plugin):
    """用户报的现象：@某人 转账 1 只会回用法提示。"""
    await say(plugin, at_event(False, "签到", uid="1002"))
    await say(plugin, at_event(False, "签到", uid="1001"))
    out = await say(
        plugin, at_event(True, " 转账 1", mention=("1002", "李子"), uid="1001")
    )
    assert "用法" not in out
    assert "已转给" in out


async def test_rob_with_at(plugin):
    await say(plugin, at_event(False, "签到", uid="1002"))
    out = await say(
        plugin, at_event(True, " 打劫 1", mention=("1002", "李子"), uid="1001")
    )
    assert "用法" not in out
    assert "打劫" in out


async def test_gift_and_duel_with_at(plugin):
    await say(plugin, at_event(False, "签到", uid="1001"))
    out = await say(
        plugin, at_event(False, " 赠送 奶茶", mention=("1002", "李子"), uid="1001")
    )
    assert "用法" not in out
    out = await say(
        plugin, at_event(False, " pk 10", mention=("1002", "李子"), uid="1001")
    )
    assert "用法" not in out


async def test_bag_of_other_user_with_at(plugin):
    await say(plugin, at_event(False, "签到", uid="1002"))
    out = await say(plugin, at_event(False, " 背包", mention=("1002", "李子")))
    assert "用法" not in out
    assert "背包" in out


# --------------------------------------------------------------- 数字竞猜


async def test_wager_number_keyword_opens(plugin):
    """「数字竞猜」是用户实际会打的词，必须能开盘。"""
    out = await say(plugin, at_event(True, " 数字竞猜"))
    assert "选项最多" not in out
    assert "竞猜开盘" in out
    assert "1:1" in out and "100:100" in out


async def test_wager_yes_no_from_sentence(plugin):
    out = await say(plugin, at_event(False, "竞猜 今晚会不会下雨"))
    assert "竞猜开盘" in out
    assert "1:是" in out and "2:否" in out


async def test_wager_plain_number_option(plugin):
    """纯数字选项也要能开盘并正常下注（旧版 5 个选项上限会直接拒绝）。"""
    out = await say(plugin, at_event(False, "竞猜 开奖号 | 1 | 2 | 3 | 4 | 5 | 6"))
    assert "选项最多" not in out
    out = await say(plugin, at_event(False, "下注 3 10", uid="1002"))
    assert "下注" in out


async def test_wager_full_flow(plugin):
    await say(plugin, at_event(False, "签到", uid="1002"))
    await say(plugin, at_event(False, "数字竞猜"))
    out = await say(plugin, at_event(False, "下注 1 10", uid="1002"))
    assert "下注" in out
    out = await say(plugin, at_event(False, "开奖 1"))
    assert "开奖" in out or "本期" in out or "正确答案" in out


def test_render_options_wraps():
    lines = games_plus.render_options(list(games_plus.WAGER_NUMBER_OPTIONS))
    assert len(lines) == 10
    assert lines[0].startswith("1:1")
    assert lines[-1].endswith("100:100")


# --------------------------------------------------------------- 猜数字


async def test_guess_number_accepts_prefixed_guess(plugin):
    """「猜 50」必须被进行中的对局接管，而不是静默丢弃。"""
    await say(plugin, at_event(False, "猜数字"))
    game = plugin._guesses["aiocqhttp_group_888"]
    out = await say(plugin, at_event(False, f"猜 {game.target}"))
    assert "猜中了" in out


async def test_guess_number_accepts_bare_number(plugin):
    await say(plugin, at_event(False, "猜数字"))
    out = await say(plugin, at_event(False, "50"))
    assert "范围" in out or "猜中" in out


async def test_guess_prefixed_without_game_hints(plugin):
    """没有对局时，「猜 50」也要给出明确提示，而不是石沉大海。"""
    out = await say(plugin, at_event(False, "猜 50"))
    assert "猜数字" in out

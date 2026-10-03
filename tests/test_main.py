"""main.py 指令层测试。

用真实 astrbot 包构造消息事件，端到端验证各指令的输入解析与输出，
重点回归「message_str 仍带指令名」导致的历史解析错误。
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
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402


class _Event(AstrMessageEvent):
    def __init__(self, *args, **kwargs):
        """记录发出的消息，便于断言免唤醒入口（走 event.send()）的输出。"""
        super().__init__(*args, **kwargs)
        self.sent: list = []

    async def send(self, *args, **kwargs):
        if args:
            self.sent.append(args[0])
        return


def make_event(text: str, uid: str = "1001", gid: str = "888", admin: bool = False):
    """构造一个已唤醒的事件，模拟 wake_prefix 剥离后的 message_str。"""
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
    event.role = "admin" if admin else "member"
    event.message_str = event.message_str[1:].strip()  # wake_prefix 被框架剥离
    return event


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
    "dice": {"enabled": True},
    "auto_reply": {"enabled": True, "rules": []},
    "permission": {"group_only": True, "cooldown_seconds": 0},
    "lucky": {"enabled": True, "reward": 15},
    "eight_ball": {"enabled": True},
    "roast": {"enabled": True},
    "trigger": {"wake_free": True, "auto_regex_games": True},
    "blackjack": {"enabled": True, "min_bet": 10, "max_bet": 500},
    "bomb": {
        "enabled": True,
        "min": 1,
        "max": 100,
        "punish": 10,
        "reward": 1,
        "timeout_seconds": 180,
    },
    "riddle": {"enabled": True, "timeout_seconds": 60, "reward": 20},
    "turtle_soup": {"enabled": True},
    "rush": {"enabled": True, "timeout_seconds": 30, "reward": 10},
    "fortune": {"enabled": True, "cost": 0},
    "shop": {"enabled": True, "allow_buy": True},
    "social": {
        "enabled": True,
        "intimacy_per_interact": 2,
        "gift_intimacy": 5,
        "duel_stake": 20,
    },
    "boss": {"enabled": True, "max_spend": 5000},
    "season": {"enabled": True},
    "pet": {"enabled": True},
    "achievements": {"enabled": True},
}


def _fresh_config() -> dict:
    """返回一份深拷贝的默认配置。

    handler 会就地修改 ``self.config``（例如把冷却时间临时调大），
    若用例之间共享同一份 dict，就会互相污染 —— 之前
    ``test_guard_cooldown`` 把 cooldown 改成 60 后，紧随其后的用例
    全都被冷却挡住了。这里每次都用全新副本。
    """
    return copy.deepcopy(DEFAULT_CONFIG)


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    """构造插件实例，并把数据目录指向 tmp_path。

    ``resolve_data_dir()`` 才是真正决定落盘位置的地方，
    因此这里替换它而不是某个常量。
    """
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    return plugin_main.InteractionPlugin(context=None, config=_fresh_config())


async def run(plugin, method: str, event, **kwargs):
    result = getattr(plugin, method)(event, **kwargs)
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


# ------------------------------------------------------- 回归：参数解析


def test_args_strips_command_name(plugin):
    assert plugin._args(make_event("接龙 互动")) == "互动"
    assert plugin._args(make_event("领取 sign")) == "sign"
    assert plugin._args(make_event("签到")) == ""
    assert plugin._args(make_event("投票 午饭 | 面 | 饭")) == "午饭 | 面 | 饭"


def test_args_prefers_longest_command_name(plugin):
    """「互动状态」不能被「互动」抢先匹配。"""
    assert plugin._args(make_event("互动状态")) == ""


@pytest.mark.asyncio
async def test_chain_start_uses_real_argument(plugin):
    """历史 bug：/接龙 互动 的起始词被解析成「接龙」。"""
    out = await run(plugin, "cmd_chain_start", make_event("接龙 互动"))
    assert "起始词：「互动」" in out
    assert plugin._chains["aiocqhttp_group_888"].last_word == "互动"


@pytest.mark.asyncio
async def test_chain_start_picks_default_when_no_arg(plugin):
    out = await run(plugin, "cmd_chain_start", make_event("接龙"))
    assert "起始词" in out
    assert plugin._chains["aiocqhttp_group_888"].last_word in (
        "互动",
        "科技",
        "群聊",
        "开心",
        "生活",
        "音乐",
        "星空",
    )


@pytest.mark.asyncio
async def test_vote_question_excludes_command(plugin):
    out = await run(
        plugin, "cmd_vote_create", make_event("投票 晚饭吃啥 | 火锅 | 烧烤")
    )
    assert "晚饭吃啥" in out and "投票 晚饭吃啥" not in out
    poll = next(iter(plugin.store.polls("aiocqhttp_group_888").values()))
    assert poll["question"] == "晚饭吃啥"
    assert poll["options"] == ["火锅", "烧烤"]


@pytest.mark.asyncio
async def test_vote_without_args_does_not_crash(plugin):
    """历史 bug：/投票 无参数时 IndexError。"""
    out = await run(plugin, "cmd_vote_create", make_event("投票"))
    assert "用法" in out


@pytest.mark.asyncio
async def test_vote_rejects_too_few_options(plugin):
    out = await run(plugin, "cmd_vote_create", make_event("投票 只有一个选项"))
    assert "用法" in out


@pytest.mark.asyncio
async def test_claim_with_code_works(plugin):
    """历史 bug：/领取 sign 把任务码解析成「领取」。"""
    sign_event = make_event("签到")
    await run(plugin, "cmd_sign", sign_event)
    out = await run(plugin, "cmd_claim", make_event("领取 sign"))
    assert "完成，获得" in out
    assert "没有这个任务" not in out


@pytest.mark.asyncio
async def test_claim_without_code_claims_all(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_claim", make_event("领取"))
    assert "一键领取" in out


# ------------------------------------------------------- 基础指令


@pytest.mark.asyncio
async def test_sign_and_double_sign(plugin):
    assert "签到成功" in await run(plugin, "cmd_sign", make_event("签到"))
    assert "已经签到" in await run(plugin, "cmd_sign", make_event("签到"))


@pytest.mark.asyncio
async def test_balance_reports_state(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_balance", make_event("积分"))
    assert "余额" in out and "等级" in out


@pytest.mark.asyncio
async def test_transfer_guards(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    assert "已转给" in await run(plugin, "cmd_transfer", make_event("转账 1002 5"))
    assert "不能给自己" in await run(plugin, "cmd_transfer", make_event("转账 1001 5"))
    assert "余额不足" in await run(
        plugin, "cmd_transfer", make_event("转账 1002 99999")
    )


@pytest.mark.asyncio
async def test_lottery_and_balance_consistency(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    out = await run(plugin, "cmd_lottery", make_event("抽奖"))
    assert "抽奖结果" in out
    balance = plugin.store.peek_user("aiocqhttp_group_888", "1001")["balance"]
    assert balance >= 0


@pytest.mark.asyncio
async def test_guess_flow(plugin):
    await run(plugin, "cmd_guess_start", make_event("猜数字"))
    game = plugin._guesses["aiocqhttp_group_888"]
    assert "猜中了" in await run(
        plugin, "cmd_guess", make_event("猜"), args=str(game.target)
    )


@pytest.mark.asyncio
async def test_guess_without_game(plugin):
    assert "没有进行中的猜数字" in await run(
        plugin, "cmd_guess", make_event("猜"), args="50"
    )


@pytest.mark.asyncio
async def test_rank_all_metrics(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    for metric in ("积分", "签到", "抽奖", "猜中", "接龙", "打劫"):
        out = await run(plugin, "cmd_rank", make_event("排行榜"), args=metric)
        assert out.strip()
    assert "可排行维度" in await run(
        plugin, "cmd_rank", make_event("排行榜"), args="乱写"
    )


@pytest.mark.asyncio
async def test_dice_and_rob(plugin):
    assert "🎲" in await run(plugin, "cmd_dice", make_event("掷骰 2 6"))
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_rob", make_event("打劫 1002 5"))
    assert "打劫" in out


@pytest.mark.asyncio
async def test_help_and_status(plugin):
    assert "玩法总览" in await run(plugin, "cmd_help", make_event("互动"))
    assert "运行状态" in await run(
        plugin, "cmd_status", make_event("互动状态", admin=True)
    )
    assert "只有管理员" in await run(plugin, "cmd_status", make_event("互动状态"))


# ------------------------------------------------------- 守卫 / 关键词


@pytest.mark.asyncio
async def test_guard_blocks_when_disabled(plugin):
    plugin.config["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_sign", make_event("签到"))


@pytest.mark.asyncio
async def test_guard_blocks_private_chat(plugin):
    assert "仅在群聊" in await run(plugin, "cmd_sign", make_event("签到", gid=""))


@pytest.mark.asyncio
async def test_guard_cooldown(plugin):
    plugin.config["permission"]["cooldown_seconds"] = 60
    await run(plugin, "cmd_sign", make_event("签到"))
    assert "太快" in await run(plugin, "cmd_balance", make_event("积分"))


@pytest.mark.asyncio
async def test_keyword_reply(plugin):
    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "你好", "reply": "你也好呀", "exact": False}
    ]
    assert "你也好呀" in await run(plugin, "on_message", make_event("你好啊"))
    assert await run(plugin, "on_message", make_event("再见")) == ""


@pytest.mark.asyncio
async def test_keyword_ignores_malformed_rules(plugin):
    plugin.config["auto_reply"]["rules"] = [
        "坏数据",
        {"keyword": "", "reply": "x"},
        None,
    ]
    assert await run(plugin, "on_message", make_event("随便")) == ""


@pytest.mark.asyncio
async def test_lifecycle_persists_and_stops_cleanly(plugin, tmp_path):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 55)
    await plugin.initialize()
    await plugin.terminate()
    assert plugin._cleanup_task is None
    files = list((tmp_path / "data").glob("*.json"))
    assert len(files) == 1 and "55" in files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_terminate_without_initialize_is_safe(plugin):
    await plugin.terminate()


@pytest.mark.asyncio
async def test_bad_config_values_fall_back(plugin):
    """配置被填成脏数据时，指令不应崩溃。"""
    plugin.config["sign_in"]["min_reward"] = "abc"
    plugin.config["lottery"]["cost"] = None
    plugin.config["rank"]["size"] = "十"
    assert await run(plugin, "cmd_sign", make_event("签到"))
    assert await run(plugin, "cmd_rank", make_event("排行榜"), args="积分")
    assert await run(plugin, "cmd_lottery", make_event("抽奖"))


# ------------------------------------------------------- 新增玩法


@pytest.mark.asyncio
async def test_lucky_number_flow(plugin):
    from astrbot_plugin_hudong import games

    out = await run(plugin, "cmd_lucky", make_event("幸运数字"))
    assert "幸运数字是" in out
    lucky = games.lucky_number(games.today_str(), "1001")
    out = await run(plugin, "cmd_lucky", make_event(f"幸运数字 {lucky}"))
    assert "猜对了" in out
    # 同一天只能领一次
    out = await run(plugin, "cmd_lucky", make_event(f"幸运数字 {lucky}"))
    assert "已经领过" in out
    # 非数字
    out = await run(plugin, "cmd_lucky", make_event("幸运数字 abc"))
    assert "整数" in out


@pytest.mark.asyncio
async def test_eight_ball_and_roast(plugin):
    assert "🔮" in await run(plugin, "cmd_eight_ball", make_event("八球 走不走"))
    assert "用法" in await run(plugin, "cmd_eight_ball", make_event("八球"))
    assert "@" in await run(plugin, "cmd_roast", make_event("扎心"))


@pytest.mark.asyncio
async def test_dice_parses_ndm_and_dirty_args(plugin):
    out = await run(plugin, "cmd_dice", make_event("掷骰 3 20"))
    assert "3d20" in out
    # 框架解析失败时 count/faces 为 0，必须回退到原始文本
    out = await run(plugin, "cmd_dice", make_event("掷骰 abc"))
    assert "1d6" in out and "不是数字" in out
    out = await run(plugin, "cmd_dice", make_event("掷骰"))
    assert "1d6" in out


@pytest.mark.asyncio
async def test_rank_invalid_metric_is_reported(plugin):
    """默认值不能吞掉非法输入（历史陷阱：默认 "积分" 会静默兜底）。"""
    out = await run(plugin, "cmd_rank", make_event("排行榜 乱写"))
    assert "可排行维度" in out
    # 不传参数时才走默认维度（没有数据时也应给出正常的空态提示）
    out = await run(plugin, "cmd_rank", make_event("排行榜"))
    assert "积分" in out


@pytest.mark.asyncio
async def test_rank_supports_new_metrics(plugin):
    await run(plugin, "cmd_dice", make_event("掷骰"))
    out = await run(plugin, "cmd_rank", make_event("排行榜 掷骰"))
    assert "掷骰" in out


@pytest.mark.asyncio
async def test_rob_clamps_amount_and_reports(plugin):
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_rob", make_event("打劫 1002 99999"))
    assert "打劫" in out
    # 金额被收敛时会有提示
    assert "上限" in out or "不值得出手" in out


@pytest.mark.asyncio
async def test_rob_broken_attacker_is_rejected(plugin):
    """余额低于赔偿倍数的打劫者被拒绝（防止零成本试错）。"""
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    out = await run(plugin, "cmd_rob", make_event("打劫 1002 1"))
    assert "先攒够" in out


@pytest.mark.asyncio
async def test_transfer_rejects_non_int(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    # 直接调用 handler 模拟框架解析失败后传入非法值
    out = await run(plugin, "cmd_transfer", make_event("转账 1002 abc"))
    assert "整数" in out


@pytest.mark.asyncio
async def test_chain_hint_throttled(plugin):
    """轮到自己但接错时的提示需要节流，避免刷屏。"""
    await run(plugin, "cmd_chain_start", make_event("接龙 互动"))
    game = plugin._chains["aiocqhttp_group_888"]
    game.last_user = "9999"  # 假装别人刚接过
    ev = make_event("随便")
    ev.message_str = "随便"
    first = await run(plugin, "_chain_submit", ev, word="随便")
    second = await run(plugin, "_chain_submit", ev, word="随便")
    assert first.strip() and not second.strip()


@pytest.mark.asyncio
async def test_new_features_respect_switches(plugin):
    plugin.config["lucky"]["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_lucky", make_event("幸运数字"))
    plugin.config["eight_ball"]["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_eight_ball", make_event("八球 x"))
    plugin.config["roast"]["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_roast", make_event("扎心"))


@pytest.mark.asyncio
async def test_keyword_rule_cache_invalidated(plugin):
    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "甲", "reply": "一", "exact": False}
    ]
    assert "一" in await run(plugin, "on_message", make_event("甲"))
    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "乙", "reply": "二", "exact": False}
    ]
    assert "二" in await run(plugin, "on_message", make_event("乙"))
    assert await run(plugin, "on_message", make_event("甲")) == ""


@pytest.mark.asyncio
async def test_balance_shows_rank_and_lucky(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_balance", make_event("积分"))
    assert "本群第" in out and "幸运数字" in out


def test_resolve_data_dir_honours_astrbot_root(tmp_path, monkeypatch):
    """数据目录必须跟随 AstrBot 的根目录，而不是固定写相对路径。

    AstrBot 的根目录取 ``ASTRBOT_ROOT`` 环境变量，缺省才是
    ``os.getcwd()``。早期实现直接写 ``Path("data") / ...``，
    在设置了 ``ASTRBOT_ROOT`` 的场景下会把数据落到错误的位置。
    """
    import sys
    import types

    fake_root = tmp_path / "elsewhere"
    fake = types.ModuleType("astrbot.core.utils.astrbot_path")
    fake.get_astrbot_plugin_data_path = lambda: str(  # type: ignore[attr-defined]
        fake_root / "data" / "plugin_data"
    )
    monkeypatch.setitem(sys.modules, "astrbot.core.utils.astrbot_path", fake)

    resolved = plugin_main.resolve_data_dir()
    assert resolved == fake_root / "data" / "plugin_data" / plugin_main.PLUGIN_NAME


def test_resolve_data_dir_uses_plugin_data_subdir(tmp_path, monkeypatch):
    """落点必须在 ``data/plugin_data/<插件名>`` 下（AstrBot 上架规范）。

    回归背景：第一版修复只取了 ``get_astrbot_data_path()``（到 ``data`` 为止），
    数据落成 ``<root>/data/<插件名>``，插件市场上架的安全检测会以
    「数据持久化位置不规范」拒绝。
    """
    import sys
    import types

    fake_root = tmp_path / "astrbot"
    fake = types.ModuleType("astrbot.core.utils.astrbot_path")
    fake.get_astrbot_plugin_data_path = lambda: str(  # type: ignore[attr-defined]
        fake_root / "data" / "plugin_data"
    )
    monkeypatch.setitem(sys.modules, "astrbot.core.utils.astrbot_path", fake)

    resolved = plugin_main.resolve_data_dir()
    assert resolved.name == "astrbot_plugin_hudong"
    assert resolved.parent.name == "plugin_data"
    assert resolved.parent.parent.name == "data"
    assert "plugin_data" in resolved.parts


def test_resolve_data_dir_falls_back_without_astrbot(tmp_path, monkeypatch):
    """拿不到 AstrBot 路径工具时回退，且回退路径同样合规。"""
    import sys

    monkeypatch.setitem(sys.modules, "astrbot.core.utils.astrbot_path", None)
    resolved = plugin_main.resolve_data_dir()
    assert resolved == Path("data") / "plugin_data" / plugin_main.PLUGIN_NAME
    assert "plugin_data" in resolved.parts


# ------------------------------------------------------- 免唤醒触发


@pytest.mark.asyncio
async def test_wake_free_triggers_without_at(plugin):
    """核心诉求：群里直接发指令就能触发，不需要 @机器人。"""
    for i, text in enumerate(("签到", "/签到", "!签到", "#签到", "／签到")):
        ev = make_event(text, uid=f"w{i}")
        ev.is_at_or_wake_command = False  # 没有 @，也没有唤醒前缀
        ev.is_wake = False
        out = await run(plugin, "on_message", ev)
        assert "签到成功" in out, (text, out)


@pytest.mark.asyncio
async def test_wake_free_command_with_args(plugin):
    await run(plugin, "on_message", make_event("签到"))
    out = await run(plugin, "on_message", make_event("排行榜 签到"))
    assert "签到排行榜" in out


@pytest.mark.asyncio
async def test_wake_free_prefers_longest_command(plugin):
    """「投票结果」不能被「投票」抢先命中。"""
    out = await run(plugin, "on_message", make_event("投票结果 abc"))
    assert "没有找到" in out


@pytest.mark.asyncio
async def test_wake_free_ignores_unrelated(plugin):
    assert await run(plugin, "on_message", make_event("今天天气不错")) == ""


@pytest.mark.asyncio
async def test_wake_free_can_be_disabled(plugin):
    plugin.config["trigger"]["wake_free"] = False
    ev = make_event("签到")
    ev.is_at_or_wake_command = False
    assert await run(plugin, "on_message", ev) == ""


@pytest.mark.asyncio
async def test_wake_free_respects_master_switch(plugin):
    plugin.config["enabled"] = False
    ev = make_event("签到")
    ev.is_at_or_wake_command = False
    assert await run(plugin, "on_message", ev) == ""


def test_normalize_strips_prefixes(plugin):
    for raw in ("签到", "/签到", "!签到", "#签到", "／签到", "  签到  "):
        assert plugin._normalize(raw) == "签到"
    assert plugin._normalize("排行榜   签到") == "排行榜 签到"


def test_resolve_command_returns_handler_and_rest(plugin):
    hit = plugin._resolve_command("排行榜 签到")
    assert hit is not None and hit[1] == "签到"
    hit = plugin._resolve_command("投票结果 abc")
    assert hit is not None and hit[1] == "abc"
    assert plugin._resolve_command("完全无关的一句话") is None
    assert plugin._resolve_command("") is None


def test_resolve_uid_variants(plugin):
    assert plugin._resolve_uid("@1001") == "1001"
    assert plugin._resolve_uid("[At:1001]") == "1001"
    assert plugin._resolve_uid("1001") == "1001"
    assert plugin._resolve_uid("") == ""
    assert plugin._resolve_uid("abc") == ""


# ------------------------------------------------------- 游戏自动接管


@pytest.mark.asyncio
async def test_auto_game_guess_plain_number(plugin):
    await run(plugin, "cmd_guess_start", make_event("猜数字"))
    game = plugin._guesses["aiocqhttp_group_888"]
    out = await run(plugin, "on_message", make_event(str(game.target)))
    assert "猜中了" in out


@pytest.mark.asyncio
async def test_auto_game_chain_plain_word(plugin):
    await run(plugin, "cmd_chain_start", make_event("接龙 互动"))
    out = await run(plugin, "on_message", make_event("动物"))
    assert "接龙成功" in out


@pytest.mark.asyncio
async def test_auto_game_bomb_plain_number(plugin):
    await run(plugin, "cmd_bomb_start", make_event("数字炸弹 1 30"))
    target = plugin._bombs["aiocqhttp_group_888"].target
    out = await run(plugin, "on_message", make_event(str(target)))
    assert "BOOM" in out


@pytest.mark.asyncio
async def test_auto_game_vote(plugin):
    await run(plugin, "cmd_vote_create", make_event("投票 测试 | A | B"))
    pid = next(iter(plugin.store.polls("aiocqhttp_group_888")))
    out = await run(plugin, "on_message", make_event(f"投 {pid} 1"))
    assert "已投票" in out


@pytest.mark.asyncio
async def test_auto_game_rush(plugin):
    await run(plugin, "cmd_rush", make_event("谁最先 冲鸭"))
    out = await run(plugin, "on_message", make_event("冲鸭冲鸭"))
    assert "抢到第一" in out


@pytest.mark.asyncio
async def test_auto_game_can_be_disabled(plugin):
    plugin.config["trigger"]["auto_regex_games"] = False
    await run(plugin, "cmd_guess_start", make_event("猜数字"))
    game = plugin._guesses["aiocqhttp_group_888"]
    assert await run(plugin, "on_message", make_event(str(game.target))) == ""


# ------------------------------------------------------- 扩展玩法指令


@pytest.mark.asyncio
async def test_blackjack_flow(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    out = await run(plugin, "cmd_blackjack", make_event("21点 50"))
    # 可能起手天胡直接结算，否则继续要牌/停牌
    if "21 点开始" in out:
        out = await run(plugin, "cmd_bj_stand", make_event("停牌"))
    assert any(k in out for k in ("你赢了", "庄家赢了", "平局", "爆牌"))


@pytest.mark.asyncio
async def test_blackjack_requires_balance(plugin):
    assert "余额不足" in await run(plugin, "cmd_blackjack", make_event("21点"))


@pytest.mark.asyncio
async def test_blackjack_no_game(plugin):
    assert "没有进行中的 21 点" in await run(plugin, "cmd_bj_stand", make_event("停牌"))
    assert "没有进行中的 21 点" in await run(plugin, "cmd_bj_hit", make_event("要牌"))


@pytest.mark.asyncio
async def test_blackjack_balance_conservation(plugin):
    """多次对局后余额不得为负。"""
    key = "aiocqhttp_group_888"
    for _ in range(40):
        plugin.store.add_balance(key, "7777", 100)
        await run(plugin, "cmd_blackjack", make_event("21点 10", uid="7777"))
        await run(plugin, "cmd_bj_stand", make_event("停牌", uid="7777"))
        assert plugin.store.get_user(key, "7777")["balance"] >= 0


@pytest.mark.asyncio
async def test_riddle_and_answer(plugin):
    await run(plugin, "cmd_riddle", make_event("猜谜"))
    answer = plugin._riddles["aiocqhttp_group_888"]["answer"]
    out = await run(plugin, "cmd_riddle_answer", make_event("谜底"))
    assert "谜底是" in out and answer in out


@pytest.mark.asyncio
async def test_riddle_guess_auto(plugin):
    await run(plugin, "cmd_riddle", make_event("猜谜"))
    answer = plugin._riddles["aiocqhttp_group_888"]["answer"]
    out = await run(plugin, "on_message", make_event(str(answer)))
    assert "答对" in out


@pytest.mark.asyncio
async def test_soup_flow(plugin):
    await run(plugin, "cmd_soup", make_event("海龟汤"))
    assert "aiocqhttp_group_888" in plugin._soups
    out = await run(plugin, "cmd_soup_answer", make_event("汤底"))
    assert "汤底：" in out


@pytest.mark.asyncio
async def test_soup_missing(plugin):
    assert "没有进行中的海龟汤" in await run(
        plugin, "cmd_soup_answer", make_event("汤底")
    )


@pytest.mark.asyncio
async def test_fortune_stable(plugin):
    first = await run(plugin, "cmd_fortune", make_event("占卜"))
    second = await run(plugin, "cmd_fortune", make_event("占卜"))
    assert first == second
    assert "运势" in first


@pytest.mark.asyncio
async def test_shop_buy_wear_bag(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    assert "互动商店" in await run(plugin, "cmd_shop", make_event("商店"))
    assert "购买成功" in await run(plugin, "cmd_buy", make_event("购买 称号·非酋"))
    assert "非酋" in await run(plugin, "cmd_bag", make_event("背包"))
    assert "已佩戴" in await run(plugin, "cmd_wear", make_event("佩戴 非酋"))
    assert "已卸下" in await run(plugin, "cmd_wear", make_event("佩戴"))


@pytest.mark.asyncio
async def test_buy_insufficient_and_unknown(plugin):
    assert "余额不足" in await run(
        plugin, "cmd_buy", make_event("购买 头像框·天选之人")
    )
    assert "没有找到" in await run(plugin, "cmd_buy", make_event("购买 不存在"))


@pytest.mark.asyncio
async def test_buy_duplicate_title_refunded(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    await run(plugin, "cmd_buy", make_event("购买 称号·非酋"))
    before = plugin.store.get_user("aiocqhttp_group_888", "1001")["balance"]
    out = await run(plugin, "cmd_buy", make_event("购买 称号·非酋"))
    after = plugin.store.get_user("aiocqhttp_group_888", "1001")["balance"]
    assert "已经拥有" in out and before == after


@pytest.mark.asyncio
async def test_gift_and_intimacy(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    out = await run(plugin, "cmd_gift", make_event("赠送 1002 奶茶"))
    assert "送出了" in out
    assert "亲密度" in await run(plugin, "cmd_intimacy", make_event("亲密度 1002"))


@pytest.mark.asyncio
async def test_gift_rejects_self_and_unknown(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    assert "不能送给自己" in await run(plugin, "cmd_gift", make_event("赠送 1001 奶茶"))
    assert "没有这种礼物" in await run(plugin, "cmd_gift", make_event("赠送 1002 游艇"))


@pytest.mark.asyncio
async def test_confess_and_duel(plugin):
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 1000)
    plugin.store.add_balance(key, "1002", 1000)
    assert "亲密度" in await run(plugin, "cmd_confess", make_event("表白 1002"))
    assert "⚔️" in await run(plugin, "cmd_duel", make_event("pk 1002 10"))


@pytest.mark.asyncio
async def test_duel_rejects_self(plugin):
    assert "不能和自己打" in await run(plugin, "cmd_duel", make_event("pk 1001"))


@pytest.mark.asyncio
async def test_random_and_joke(plugin):
    assert "我选" in await run(plugin, "cmd_random", make_event("随机 火锅|烧烤"))
    assert "😂" in await run(plugin, "cmd_joke", make_event("笑话"))


@pytest.mark.asyncio
async def test_random_requires_two_options(plugin):
    assert "至少" in await run(plugin, "cmd_random", make_event("随机 只有一个"))


@pytest.mark.asyncio
async def test_new_features_respect_master_switch(plugin):
    plugin.config["enabled"] = False
    for method, text in (
        ("cmd_blackjack", "21点"),
        ("cmd_bomb_start", "数字炸弹"),
        ("cmd_riddle", "猜谜"),
        ("cmd_soup", "海龟汤"),
        ("cmd_rush", "谁最快"),
        ("cmd_fortune", "占卜"),
        ("cmd_shop", "商店"),
        ("cmd_gift", "赠送 1002 奶茶"),
        ("cmd_duel", "pk 1002"),
    ):
        out = await run(plugin, method, make_event(text))
        assert "已关闭" in out, (method, out)


# ------------------------------------------------------- v1.3.0 标签体系


@pytest.mark.asyncio
async def test_tags_overview_shows_summary(plugin):
    out = await run(plugin, "cmd_tags", make_event("标签"))
    assert "标签收集" in out
    assert "最接近完成" in out
    assert "%" in out


@pytest.mark.asyncio
async def test_tags_filter_by_rarity_excludes_others(plugin):
    out = await run(plugin, "cmd_tags", make_event("标签 稀有"))
    assert "稀有" in out
    assert "普通" not in out


@pytest.mark.asyncio
async def test_tags_filter_by_category(plugin):
    out = await run(plugin, "cmd_tags", make_event("标签 日常"))
    assert "日常" in out
    assert "签到" in out


@pytest.mark.asyncio
async def test_tags_search_normalizes_punctuation(plugin):
    """「标签 搜索 签到！」尾部标点不能被带进关键词，否则查不到。"""
    out = await run(plugin, "cmd_tags", make_event("标签 搜索 签到！"))
    assert "关键词「签到」" in out
    assert "签到七日" in out


@pytest.mark.asyncio
async def test_tags_search_no_match(plugin):
    out = await run(plugin, "cmd_tags", make_event("标签 搜索 完全不存在的词"))
    assert "没有匹配" in out


@pytest.mark.asyncio
async def test_tag_search_command_requires_keyword(plugin):
    out = await run(plugin, "cmd_tag_search", make_event("标签搜索"))
    assert "用法" in out


@pytest.mark.asyncio
async def test_tag_search_command_delegates(plugin):
    out = await run(plugin, "cmd_tag_search", make_event("标签搜索 抽奖"))
    assert "抽奖" in out


@pytest.mark.asyncio
async def test_tags_parse_free_word_order(plugin):
    """筛选词与关键词的相对顺序不影响解析结果。"""
    query, _cat, rarity, _locked, _unlocked = plugin._parse_tag_args("稀有 抽奖")
    assert query == "抽奖"
    assert rarity == "rare"

    query, _cat, rarity, _locked, _unlocked = plugin._parse_tag_args("抽奖 稀有")
    assert query == "抽奖"
    assert rarity == "rare"


@pytest.mark.asyncio
async def test_tags_parse_search_swallows_rest(plugin):
    """「搜索」之后的全部内容都算关键词 —— 这是有意为之。

    否则用户搜「搜索 签到 七日」时，第二个词会被误判成筛选条件。
    """
    query, _cat, _rar, _locked, _unlocked = plugin._parse_tag_args(
        "稀有 搜索 抽奖 稀有"
    )
    assert query == "抽奖 稀有"


@pytest.mark.asyncio
async def test_tags_parse_only_unlocked(plugin):
    query, _cat, _rar, locked, unlocked = plugin._parse_tag_args("已获得")
    assert query == "" and unlocked is True and locked is False


@pytest.mark.asyncio
async def test_achievements_page_includes_rarity_badge(plugin):
    out = await run(plugin, "cmd_achievements", make_event("成就"))
    assert "稀有" in out or "传说" in out


# ------------------------------------------------------- v1.3.0 群 Boss 战


@pytest.mark.asyncio
async def test_boss_start_creates_fight(plugin):
    out = await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    assert "群 Boss 战开始" in out
    assert "史莱姆王" in out
    assert "aiocqhttp_group_888" in plugin._bosses


@pytest.mark.asyncio
async def test_boss_start_rejects_unknown_name(plugin):
    out = await run(plugin, "cmd_boss", make_event("讨伐 不存在"))
    assert "没有这个 Boss" in out
    assert "aiocqhttp_group_888" not in plugin._bosses


@pytest.mark.asyncio
async def test_boss_start_rejects_duplicate(plugin):
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    out = await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    assert "已在讨伐" in out


@pytest.mark.asyncio
async def test_boss_attack_without_fight(plugin):
    out = await run(plugin, "cmd_boss_attack", make_event("攻击 100"))
    assert "没有进行中的讨伐" in out


@pytest.mark.asyncio
async def test_boss_attack_zero_spend(plugin):
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    out = await run(plugin, "cmd_boss_attack", make_event("攻击 0"))
    assert "大于 0" in out


@pytest.mark.asyncio
async def test_boss_attack_caps_spend(plugin):
    plugin.config["boss"]["max_spend"] = 100
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 10000)
    out = await run(plugin, "cmd_boss_attack", make_event("攻击 500"))
    assert "最多投入" in out


@pytest.mark.asyncio
async def test_boss_attack_insufficient_balance(plugin):
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    out = await run(plugin, "cmd_boss_attack", make_event("攻击 100"))
    assert "积分不足" in out


@pytest.mark.asyncio
async def test_boss_attack_deducts_balance(plugin):
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 1000)
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    await run(plugin, "cmd_boss_attack", make_event("攻击 300"))
    assert plugin.store.get_user("aiocqhttp_group_888", "1001")["balance"] == 700


@pytest.mark.asyncio
async def test_boss_kill_is_net_negative_and_conserved(plugin):
    """击杀后分得的总额必须等于奖池，且小于投入（合作有损耗）。"""
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 2000)
    plugin.store.add_balance(key, "1002", 2000)
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    await run(
        plugin,
        "cmd_boss_attack",
        make_event("攻击 500"),
    )
    await run(plugin, "cmd_boss_attack", make_event("攻击 1500", uid="1002"))
    # 史莱姆王 1200 HP：1001 投入 500 打 500 点，1002 请求 1500 点，
    # 但 Boss 只剩 700 HP，所以只结算 700 点、退回 800。
    # 总投入 1200，分得奖池 700 ⇒ 群总资产净减 500（合作有损耗）。
    u1 = plugin.store.get_user(key, "1001")
    u2 = plugin.store.get_user(key, "1002")
    pool_paid = u1["boss_gold"] + u2["boss_gold"]
    assert pool_paid == 700, f"分得的总额必须等于奖池 700，实际 {pool_paid}"

    # 溢出退款：1002 实际只该被扣 700（成就奖励另算，单列核对）
    ach_bonus = sum(
        reward
        for code, _n, _f, _t, reward in plugin_main.games_plus.ACHIEVEMENTS
        if code in u2["achievements"]
    )
    assert u2["balance"] == 2000 - 700 + u2["boss_gold"] + ach_bonus
    assert u2["boss_damage"] == 700, "记功要按实际打出的伤害，不是请求值"
    assert u1["boss_damage"] == 500

    total_after = u1["balance"] + u2["balance"]
    assert total_after < 4000, "Boss 战必须让群总资产净减"
    assert key not in plugin._bosses, "击杀后对局应被回收"
    assert u2["boss_kill"] == 1
    assert u1["boss_kill"] == 0, "只给最后一击记击杀数"


@pytest.mark.asyncio
async def test_boss_attack_below_cost_refunds(plugin):
    """投入不足以折算 1 点伤害时，必须原样退回，不能沉默扣款。"""
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 100)
    plugin._bosses[key] = plugin_main.games_world.BossFight.create("slime", 0)
    plugin._bosses[key].cost = 50  # 人为抬高门槛
    out = await run(plugin, "cmd_boss_attack", make_event("攻击 10"))
    assert "至少投入" in out
    assert plugin.store.get_user(key, "1001")["balance"] == 100, "未造成伤害必须退款"


@pytest.mark.asyncio
async def test_boss_info_without_fight(plugin):
    out = await run(plugin, "cmd_boss_info", make_event("讨伐状态"))
    assert "没有进行中的讨伐" in out


@pytest.mark.asyncio
async def test_boss_info_lists_damage_ranking(plugin):
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 500)
    await run(plugin, "cmd_boss", make_event("讨伐 slime"))
    await run(plugin, "cmd_boss_attack", make_event("攻击 100"))
    out = await run(plugin, "cmd_boss_info", make_event("讨伐状态"))
    assert "伤害榜" in out
    assert "100" in out


# ------------------------------------------------------- v1.3.0 赛季


@pytest.mark.asyncio
async def test_season_status_shows_tier(plugin):
    out = await run(plugin, "cmd_season", make_event("赛季"))
    assert "赛季" in out
    assert "段位" in out


@pytest.mark.asyncio
async def test_season_claim_credits_balance(plugin):
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 2000)
    before = plugin.store.get_user(key, "1001")["balance"]
    out = await run(plugin, "cmd_season", make_event("赛季 领取"))
    assert "领取了" in out
    after = plugin.store.get_user(key, "1001")["balance"]
    assert after > before


@pytest.mark.asyncio
async def test_season_claim_is_idempotent(plugin):
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 2000)
    await run(plugin, "cmd_season", make_event("赛季 领取"))
    after_first = plugin.store.get_user(key, "1001")["balance"]
    out = await run(plugin, "cmd_season", make_event("赛季 领取"))
    assert "已领满" in out
    assert plugin.store.get_user(key, "1001")["balance"] == after_first


@pytest.mark.asyncio
async def test_season_base_tier_is_claimable(plugin):
    """青铜段位有底奖（设计如此：有进度就有正反馈），不是错误分支。"""
    out = await run(plugin, "cmd_season", make_event("赛季 领取"))
    assert "领取了" in out
    assert "青铜" in out


@pytest.mark.asyncio
async def test_season_claim_after_full_claim(plugin):
    """已领满后再领必须被拒绝，且不动余额。"""
    key = "aiocqhttp_group_888"
    await run(plugin, "cmd_season", make_event("赛季 领取"))
    after = plugin.store.get_user(key, "1001")["balance"]
    out = await run(plugin, "cmd_season", make_event("赛季 领取"))
    assert "已领满" in out
    assert plugin.store.get_user(key, "1001")["balance"] == after


@pytest.mark.asyncio
async def test_season_gain_resets_on_new_season(plugin):
    """跨赛季必须清零增量，但不该动总积分。"""
    key = "aiocqhttp_group_888"
    plugin.store.add_balance(key, "1001", 500)
    user = plugin.store.get_user(key, "1001")
    user["season_tag"] = "2000-01"  # 伪造一个旧赛季
    out = await run(plugin, "cmd_season", make_event("赛季"))
    assert "2026-" in out or "202" in out
    assert plugin.store.get_user(key, "1001")["season_gain"] == 0
    assert plugin.store.get_user(key, "1001")["balance"] == 500


# ------------------------------------------------------- v1.3.0 宠物


@pytest.mark.asyncio
async def test_pet_adopt_then_feed(plugin):
    out = await run(plugin, "cmd_pet", make_event("宠物"))
    assert "领养" in out
    out = await run(plugin, "cmd_pet_feed", make_event("喂食"))
    assert "照料了" in out
    assert plugin.store.get_user("aiocqhttp_group_888", "1001")["pet"]["care"] == 1


@pytest.mark.asyncio
async def test_pet_feed_has_cooldown(plugin):
    await run(plugin, "cmd_pet", make_event("宠物"))
    await run(plugin, "cmd_pet_feed", make_event("喂食"))
    out = await run(plugin, "cmd_pet_feed", make_event("喂食"))
    assert "别喂太急" in out
    assert plugin.store.get_user("aiocqhttp_group_888", "1001")["pet"]["care"] == 1


@pytest.mark.asyncio
async def test_pet_feed_without_adoption(plugin):
    out = await run(plugin, "cmd_pet_feed", make_event("喂食"))
    assert "还没有宠物" in out


@pytest.mark.asyncio
async def test_pet_name_requires_adoption(plugin):
    out = await run(plugin, "cmd_pet_name", make_event("起名 小互动"))
    assert "还没有宠物" in out


@pytest.mark.asyncio
async def test_pet_name_empty(plugin):
    await run(plugin, "cmd_pet", make_event("宠物"))
    out = await run(plugin, "cmd_pet_name", make_event("起名"))
    assert "用法" in out


@pytest.mark.asyncio
async def test_pet_name_too_long(plugin):
    await run(plugin, "cmd_pet", make_event("宠物"))
    out = await run(plugin, "cmd_pet_name", make_event("起名 " + "超" * 20))
    assert "太长" in out


@pytest.mark.asyncio
async def test_pet_name_success(plugin):
    await run(plugin, "cmd_pet", make_event("宠物"))
    out = await run(plugin, "cmd_pet_name", make_event("起名 小互动"))
    assert "小互动" in out
    assert (
        plugin.store.get_user("aiocqhttp_group_888", "1001")["pet"]["name"] == "小互动"
    )


@pytest.mark.asyncio
async def test_pet_gain_is_bounded(plugin):
    """无论怎么喂，单次产出都不能超过设计上限（防印钞）。"""
    key = "aiocqhttp_group_888"
    await run(plugin, "cmd_pet", make_event("宠物"))
    for _i in range(30):
        user = plugin.store.get_user(key, "1001")
        user["pet"]["fed_at"] = 0  # 绕过冷却，直接压测收益
        await run(plugin, "cmd_pet_feed", make_event("喂食"))
    total = plugin.store.get_user(key, "1001")["balance"]
    assert total <= 30 * plugin_main.games_world.PET_MAX_GAIN


@pytest.mark.asyncio
async def test_bonus_shows_new_sections(plugin):
    out = await run(plugin, "cmd_bonus", make_event("我的加成"))
    assert "标签" in out
    assert "赛季" in out
    assert "宠物" in out

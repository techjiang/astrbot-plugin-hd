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
    async def send(self, *args, **kwargs):  # pragma: no cover - 仅满足接口
        return None


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
    return "\n".join(
        item.get_plain_text() if hasattr(item, "get_plain_text") else str(item)
        for item in items
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
    assert "已转给" in await run(
        plugin, "cmd_transfer", make_event("转账 1002 5"), target="1002", amount=5
    )
    assert "不能给自己" in await run(
        plugin, "cmd_transfer", make_event("转账 1001 5"), target="1001", amount=5
    )
    assert "余额不足" in await run(
        plugin,
        "cmd_transfer",
        make_event("转账 1002 99999"),
        target="1002",
        amount=99999,
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
        plugin, "cmd_guess", make_event("猜"), number=game.target
    )


@pytest.mark.asyncio
async def test_guess_without_game(plugin):
    assert "没有进行中的猜数字" in await run(
        plugin, "cmd_guess", make_event("猜"), number=50
    )


@pytest.mark.asyncio
async def test_rank_all_metrics(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    for metric in ("积分", "签到", "抽奖", "猜中", "接龙", "打劫"):
        out = await run(plugin, "cmd_rank", make_event("排行榜"), metric=metric)
        assert out.strip()
    assert "可排行维度" in await run(
        plugin, "cmd_rank", make_event("排行榜"), metric="乱写"
    )


@pytest.mark.asyncio
async def test_dice_and_rob(plugin):
    assert "🎲" in await run(plugin, "cmd_dice", make_event("掷骰"), count=2, faces=6)
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_rob", make_event("打劫"), target="1002", amount=5)
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
    assert "你也好呀" in await run(plugin, "on_keyword", make_event("你好啊"))
    assert await run(plugin, "on_keyword", make_event("再见")) == ""


@pytest.mark.asyncio
async def test_keyword_ignores_malformed_rules(plugin):
    plugin.config["auto_reply"]["rules"] = [
        "坏数据",
        {"keyword": "", "reply": "x"},
        None,
    ]
    assert await run(plugin, "on_keyword", make_event("随便")) == ""


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
    assert await run(plugin, "cmd_rank", make_event("排行榜"), metric="积分")
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
    assert "🔮" in await run(
        plugin, "cmd_eight_ball", make_event("八球 走不走"), question="走不走"
    )
    assert "用法" in await run(
        plugin, "cmd_eight_ball", make_event("八球"), question=""
    )
    assert "@" in await run(plugin, "cmd_roast", make_event("扎心"), target="")


@pytest.mark.asyncio
async def test_dice_parses_ndm_and_dirty_args(plugin):
    out = await run(plugin, "cmd_dice", make_event("掷骰 3 20"), count=3, faces=20)
    assert "3d20" in out
    # 框架解析失败时 count/faces 为 0，必须回退到原始文本
    out = await run(plugin, "cmd_dice", make_event("掷骰 abc"), count=0, faces=0)
    assert "1d6" in out and "不是数字" in out
    out = await run(plugin, "cmd_dice", make_event("掷骰"), count=0, faces=0)
    assert "1d6" in out


@pytest.mark.asyncio
async def test_rank_invalid_metric_is_reported(plugin):
    """默认值不能吞掉非法输入（历史陷阱：默认 "积分" 会静默兜底）。"""
    out = await run(plugin, "cmd_rank", make_event("排行榜 乱写"), metric="乱写")
    assert "可排行维度" in out
    # 不传参数时才走默认维度（没有数据时也应给出正常的空态提示）
    out = await run(plugin, "cmd_rank", make_event("排行榜"), metric="")
    assert "积分" in out


@pytest.mark.asyncio
async def test_rank_supports_new_metrics(plugin):
    await run(plugin, "cmd_dice", make_event("掷骰"), count=1, faces=6)
    out = await run(plugin, "cmd_rank", make_event("排行榜 掷骰"), metric="掷骰")
    assert "掷骰" in out


@pytest.mark.asyncio
async def test_rob_clamps_amount_and_reports(plugin):
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    await run(plugin, "cmd_sign", make_event("签到"))
    out = await run(plugin, "cmd_rob", make_event("打劫"), target="1002", amount=99999)
    assert "打劫" in out
    # 金额被收敛时会有提示
    assert "上限" in out or "不值得出手" in out


@pytest.mark.asyncio
async def test_rob_broken_attacker_is_rejected(plugin):
    """余额低于赔偿倍数的打劫者被拒绝（防止零成本试错）。"""
    await run(plugin, "cmd_sign", make_event("签到", uid="1002"))
    out = await run(plugin, "cmd_rob", make_event("打劫"), target="1002", amount=1)
    assert "先攒够" in out


@pytest.mark.asyncio
async def test_transfer_rejects_non_int(plugin):
    await run(plugin, "cmd_sign", make_event("签到"))
    # 直接调用 handler 模拟框架解析失败后传入非法值
    out = await run(
        plugin, "cmd_transfer", make_event("转账"), target="1002", amount="abc"
    )
    assert "整数" in out


@pytest.mark.asyncio
async def test_chain_hint_throttled(plugin):
    """轮到自己但接错时的提示需要节流，避免刷屏。"""
    await run(plugin, "cmd_chain_start", make_event("接龙 互动"))
    game = plugin._chains["aiocqhttp_group_888"]
    game.last_user = "9999"  # 假装别人刚接过
    ev = make_event("随便")
    ev.message_str = "随便"
    first = await run(plugin, "on_chain_message", ev)
    second = await run(plugin, "on_chain_message", ev)
    assert first.strip() and not second.strip()


@pytest.mark.asyncio
async def test_new_features_respect_switches(plugin):
    plugin.config["lucky"]["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_lucky", make_event("幸运数字"))
    plugin.config["eight_ball"]["enabled"] = False
    assert "已关闭" in await run(
        plugin, "cmd_eight_ball", make_event("八球 x"), question="x"
    )
    plugin.config["roast"]["enabled"] = False
    assert "已关闭" in await run(plugin, "cmd_roast", make_event("扎心"), target="")


@pytest.mark.asyncio
async def test_keyword_rule_cache_invalidated(plugin):
    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "甲", "reply": "一", "exact": False}
    ]
    assert "一" in await run(plugin, "on_keyword", make_event("甲"))
    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "乙", "reply": "二", "exact": False}
    ]
    assert "二" in await run(plugin, "on_keyword", make_event("乙"))
    assert await run(plugin, "on_keyword", make_event("甲")) == ""


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

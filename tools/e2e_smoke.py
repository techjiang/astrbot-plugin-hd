"""端到端冒烟测试：用真实 astrbot 框架加载插件并逐个指令调用。

与 ``tests/`` 下的单测不同，这里模拟的是 AstrBot 真实链路：

1. 把仓库根目录注册成 ``astrbot_plugin_hudong`` 包；
2. 从 ``star_handlers_registry`` 读取**真实注册结果**（指令名/别名）；
3. 构造真实的 ``AstrMessageEvent``，用 ``CommandFilter`` 走一遍
   「过滤 → 参数解析」，拿到与框架完全一致的 ``parsed_params``；
4. 按框架的方式调用 handler（未解析到的参数才回落到默认值）。

用法：``python tools/e2e_smoke.py``，全部通过时退出码为 0。
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "astrbot_plugin_hudong"


def bootstrap() -> None:
    """把仓库根目录注册成 astrbot_plugin_hudong 包。"""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)


from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star_handler import star_handlers_registry

bootstrap()

from astrbot_plugin_hudong import games
from astrbot_plugin_hudong import main as plugin_main

CONFIG = {
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


class _Event(AstrMessageEvent):
    async def send(self, *a, **k):  # pragma: no cover
        return None


def make_event(text: str, uid: str = "1001", gid: str = "888", admin: bool = False):
    """构造已唤醒的事件（wake_prefix 已被框架剥掉）。"""
    msg = AstrBotMessage()
    msg.message = [Plain(f"/{text}")]
    msg.sender = MessageMember(user_id=uid, nickname=f"用户{uid}")
    msg.self_id = "999"
    msg.group_id = gid
    msg.type = MessageType.GROUP_MESSAGE if gid else MessageType.FRIEND_MESSAGE
    msg.session_id = gid or uid
    ev = _Event(
        f"/{text}",
        msg,
        PlatformMetadata(name="aiocqhttp", id="aiocqhttp", description=""),
        msg.session_id,
    )
    ev.is_at_or_wake_command = True
    ev.is_wake = True
    ev.role = "admin" if admin else "member"
    ev.message_str = ev.message_str[1:].strip()
    return ev


class Harness:
    """按 AstrBot 的方式调用 handler（含真实参数解析）。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.handlers = list(
            star_handlers_registry.get_handlers_by_module_name(f"{PKG}.main")
        )
        self._by_name = {h.handler_name: h for h in self.handlers}

    def parsed_params(self, handler_name: str, event) -> dict:
        """跑一遍该 handler 的 CommandFilter，取出框架解析出的参数。"""
        md = self._by_name.get(handler_name)
        if md is None:
            return {}
        command_filters = [f for f in md.event_filters if isinstance(f, CommandFilter)]
        if not command_filters:
            return {}
        cf = command_filters[0]
        try:
            ok = cf.filter(event, {})
        except ValueError:
            # 参数类型不合法时框架会抛错 → 该 handler 被跳过
            return {}
        if not ok:
            return {}
        return event.get_extra("parsed_params", {}) or {}

    async def call(self, handler_name: str, event) -> str:
        """调用 handler，参数与框架保持一致（解析不到的走默认值）。"""
        md = self._by_name.get(handler_name)
        assert md is not None, f"未注册的 handler: {handler_name}"
        params = self.parsed_params(handler_name, event)
        sig = inspect.signature(md.handler)
        kwargs = {}
        for p in sig.parameters.values():
            if p.name in ("self", "event"):
                continue
            if p.name in params:
                kwargs[p.name] = params[p.name]
            elif p.default is inspect.Parameter.empty:
                raise AssertionError(
                    f"{handler_name} 的必填参数 {p.name} 既没解析到也没有默认值"
                )
        res = md.handler(self.plugin, event, **kwargs)
        items = []
        if hasattr(res, "__aiter__"):
            async for item in res:
                if item is not None:
                    items.append(item)
        else:
            item = await res
            if item is not None:
                items.append(item)
        texts = []
        for item in items:
            if isinstance(item, MessageEventResult) or hasattr(item, "get_plain_text"):
                texts.append(item.get_plain_text())
        return "\n".join(texts)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hudong-e2e-"))
    plugin_main.DATA_SUBDIR = tmp / "data"
    plugin = plugin_main.InteractionPlugin(context=None, config=dict(CONFIG))
    await plugin.initialize()
    h = Harness(plugin)

    results: list[tuple[str, bool, str]] = []

    def record(label: str, text: str, must: tuple[str, ...] = ()) -> None:
        ok = all(m in text for m in must) if must else bool(text.strip())
        results.append((label, ok, text.replace("\n", " | ")[:110]))

    # ---------- 1) 真实注册结果 ----------
    cmds: dict[str, list[str]] = {}
    for handler in h.handlers:
        for f in handler.event_filters:
            if isinstance(f, CommandFilter):
                cmds[handler.handler_name] = sorted(f.get_complete_command_names())
    print(f"[注册] {len(h.handlers)} 个 handler，其中 {len(cmds)} 个指令处理器")
    for name in sorted(cmds):
        print(f"  {name:<18} {cmds[name]}")

    expected = {
        "cmd_sign",
        "cmd_balance",
        "cmd_transfer",
        "cmd_lottery",
        "cmd_guess_start",
        "cmd_guess",
        "cmd_chain_start",
        "cmd_vote_create",
        "cmd_vote_cast",
        "cmd_vote_result",
        "cmd_rank",
        "cmd_quest",
        "cmd_claim",
        "cmd_dice",
        "cmd_rob",
        "cmd_lucky",
        "cmd_eight_ball",
        "cmd_roast",
        "cmd_help",
        "cmd_status",
    }
    missing = expected - set(cmds)
    assert not missing, f"指令未注册: {missing}"

    # ---------- 2) 框架参数解析行为 ----------
    print("\n[参数解析] 真实 CommandFilter")
    cases = [
        ("签到", "cmd_sign", {}),
        ("排行榜 签到", "cmd_rank", {"metric": "签到"}),
        ("掷骰 3 20", "cmd_dice", {"count": 3, "faces": 20}),
        ("接龙 互动", "cmd_chain_start", {}),
        ("互动状态", "cmd_status", {}),
    ]
    for text, handler_name, expect in cases:
        got = h.parsed_params(handler_name, make_event(text))
        assert got == expect, (text, handler_name, got, expect)
    # 非法参数：框架解析失败 → handlers_parsed_params 为空 → 走默认值
    assert h.parsed_params("cmd_rank", make_event("排行榜 乱写")) == {"metric": "乱写"}
    assert h.parsed_params("cmd_dice", make_event("掷骰 abc def")) == {}
    print("  参数解析与框架一致 ✅")

    # ---------- 3) 指令端到端 ----------
    print("\n[指令] 逐个调用（走 CommandFilter 解析）")

    async def run(label: str, text: str, must: tuple[str, ...] = (), **kw) -> str:
        handler_name = kw.pop("handler")
        out = await h.call(handler_name, make_event(text, **kw))
        record(label, out, must)
        return out

    await run("签到", "签到", ("签到成功",), handler="cmd_sign")
    await run("重复签到", "签到", ("已经签到",), handler="cmd_sign")
    await run("积分", "积分", ("余额", "等级", "幸运数字"), handler="cmd_balance")
    await run(
        "每日任务",
        "每日任务",
        ("每日任务", "完成 1 次签到（1/1）"),
        handler="cmd_quest",
    )
    await run("领取单个", "领取 sign", ("完成，获得",), handler="cmd_claim")
    await run("领取一键", "领取", (), handler="cmd_claim")
    await run("抽奖(余额不足)", "抽奖", ("积分不足",), handler="cmd_lottery")

    # 猜数字
    await run("猜数字开始", "猜数字", ("猜数字开始",), handler="cmd_guess_start")
    game = plugin._guesses["aiocqhttp_group_888"]
    await run("猜中", f"猜 {game.target}", ("猜中了",), handler="cmd_guess")
    await run("猜(无局)", "猜 50", ("没有进行中的猜数字",), handler="cmd_guess")

    # 接龙
    await run("接龙开始", "接龙 互动", ("起始词：「互动」",), handler="cmd_chain_start")
    ev = make_event("动作")
    ev.message_str = "动作"
    record("接龙提交", await h.call("on_chain_message", ev), ("接龙成功",))

    # 投票
    await run(
        "发起投票",
        "投票 晚饭吃啥 | 火锅 | 烧烤",
        ("晚饭吃啥", "编号"),
        handler="cmd_vote_create",
    )
    poll_id = next(iter(plugin.store.polls("aiocqhttp_group_888")))
    await run("投票", f"投 {poll_id} 1", ("已投票",), handler="cmd_vote_cast")
    await run(
        "投票结果", f"投票结果 {poll_id}", ("当前领先",), handler="cmd_vote_result"
    )
    await run("投票列表", "投票结果", ("本群投票列表",), handler="cmd_vote_result")
    await run("无参投票", "投票", ("用法",), handler="cmd_vote_create")

    # 掷骰
    await run("掷骰 NdM", "掷骰 2 20", ("2d20",), handler="cmd_dice")
    await run("掷骰 脏参", "掷骰 abc", ("1d6", "不是数字"), handler="cmd_dice")
    await run("掷骰 无参", "掷骰", ("1d6",), handler="cmd_dice")

    # 打劫 / 转账
    await run("打劫自己", "打劫 1001 5", ("不能打劫自己",), handler="cmd_rob")
    await run("打劫无档案", "打劫 5555 5", ("还没有档案",), handler="cmd_rob")
    await run("转账", "转账 1002 1", ("已转给",), handler="cmd_transfer")
    await run("转账脏参", "转账 1002 abc", (), handler="cmd_transfer")

    # 新玩法
    await run("幸运数字", "幸运数字", ("幸运数字",), handler="cmd_lucky")
    lucky = games.lucky_number(games.today_str(), "1001")
    await run("幸运数字命中", f"幸运数字 {lucky}", ("猜对了",), handler="cmd_lucky")
    await run("幸运数字重复", f"幸运数字 {lucky}", ("已经领过",), handler="cmd_lucky")
    await run("八球", "八球 今天要加班吗", ("🔮",), handler="cmd_eight_ball")
    await run("八球空参", "八球", ("用法",), handler="cmd_eight_ball")
    await run("扎心", "扎心", ("@",), handler="cmd_roast")

    # 排行
    for metric in ("积分", "签到", "抽奖", "猜中", "接龙", "打劫", "掷骰", "幸运"):
        await run(f"排行-{metric}", f"排行榜 {metric}", (), handler="cmd_rank")
    await run("排行-无参", "排行榜", ("排行榜",), handler="cmd_rank")
    await run("排行-非法", "排行榜 乱写", ("可排行维度",), handler="cmd_rank")

    await run("帮助", "互动", ("玩法总览",), handler="cmd_help")
    await run(
        "状态-管理员", "互动状态", ("运行状态",), handler="cmd_status", admin=True
    )
    await run("状态-非管理员", "互动状态", ("只有管理员",), handler="cmd_status")

    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "你好", "reply": "你也好呀", "exact": False}
    ]
    record("关键词", await h.call("on_keyword", make_event("你好啊")), ("你也好呀",))

    # ---------- 4) 守卫 ----------
    plugin.config["enabled"] = False
    await run("总开关关闭", "签到", ("已关闭",), handler="cmd_sign")
    plugin.config["enabled"] = True
    await run("私聊限制", "签到", ("仅在群聊",), handler="cmd_sign", gid="")
    plugin.config["permission"]["cooldown_seconds"] = 60
    await h.call("cmd_sign", make_event("签到"))
    await run("冷却", "积分", ("太快",), handler="cmd_balance")
    plugin.config["permission"]["cooldown_seconds"] = 0

    # ---------- 5) 脏配置兜底 ----------
    plugin.config["sign_in"]["min_reward"] = "abc"
    plugin.config["lottery"]["cost"] = None
    plugin.config["rank"]["size"] = "十"
    plugin.config["guess_number"]["max"] = "一百"
    await run("脏配置-签到", "签到", (), handler="cmd_sign")
    await run("脏配置-抽奖", "抽奖", (), handler="cmd_lottery")
    await run("脏配置-排行", "排行榜", (), handler="cmd_rank")
    await run("脏配置-猜数字", "猜数字", ("猜数字开始",), handler="cmd_guess_start")

    # ---------- 6) 生命周期 ----------
    await plugin.terminate()
    files = list((tmp / "data").glob("*.json"))
    assert files, "卸载后没有数据文件"
    print(f"\n[生命周期] 卸载刷盘成功，{len(files)} 个数据文件")

    # ---------- 汇总 ----------
    failed = [r for r in results if not r[1]]
    print(f"\n[结果] {len(results) - len(failed)}/{len(results)} 通过")
    for label, ok, text in results:
        print(f"  {'PASS' if ok else 'FAIL'} {label}: {text}")
    if failed:
        print("\n失败项：")
        for label, _, text in failed:
            print(f"  ✗ {label}: {text}")
        return 1
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

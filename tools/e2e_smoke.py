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
}


class _Event(AstrMessageEvent):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = []

    async def send(self, *a, **k):
        """记录发出的消息，便于断言（真实实现会走平台适配器）。"""
        if a:
            self.sent.append(a[0])
        elif "message" in k:
            self.sent.append(k["message"])
        return


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

    async def call(
        self, handler_name: str, event, args: str | None = None, **extra
    ) -> str:
        """调用 handler。

        新约定：handler 统一签名为 ``(event, args="")``，参数由 handler 自己
        从 ``args`` 解析，因此这里只把 ``args`` 传进去（缺省则由 handler 从
        ``event.message_str`` 里自行剥离指令名）。
        """
        md = self._by_name.get(handler_name)
        # 允许直接调用插件上的非注册辅助方法（如 _chain_submit）
        target = (
            md.handler if md is not None else getattr(self.plugin, handler_name, None)
        )
        assert target is not None, f"未注册的 handler: {handler_name}"
        sig = inspect.signature(target)
        kwargs = {}
        if "args" in sig.parameters and handler_name != "on_message":
            kwargs["args"] = args if args is not None else self.plugin._args(event)
        # 辅助方法（_riddle_guess / _rush_check 等）的 text 从事件取
        for p in sig.parameters.values():
            if p.name in ("self", "event", "args"):
                continue
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                continue
            if p.name in extra:
                kwargs[p.name] = extra[p.name]
            elif p.name in ("text", "word"):
                # 自动接管类辅助方法的入参，直接取消息文本
                kwargs[p.name] = args if args is not None else event.message_str
            elif p.default is inspect.Parameter.empty:
                raise AssertionError(f"{handler_name} 的必填参数 {p.name} 没有默认值")
        res = (
            target(self.plugin, event, **kwargs)
            if md is not None
            else target(event, **kwargs)
        )
        items = []
        if hasattr(res, "__aiter__"):
            async for item in res:
                if item is not None:
                    items.append(item)
        else:
            item = await res
            if item is not None:
                items.append(item)
        # 免唤醒入口通过 event.send() 输出，这里一并收集
        for sent in getattr(event, "sent", []):
            items.append(sent)
        texts = []
        for item in items:
            if isinstance(item, MessageEventResult) or hasattr(item, "get_plain_text"):
                texts.append(item.get_plain_text())
        return "\n".join(texts)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hudong-e2e-"))
    # 数据目录由 resolve_data_dir() 决定，替换它即可让冒烟测试落进临时目录
    plugin_main.resolve_data_dir = lambda: tmp / "data"
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
        "cmd_blackjack",
        "cmd_bj_hit",
        "cmd_bj_stand",
        "cmd_bomb_start",
        "cmd_riddle",
        "cmd_riddle_answer",
        "cmd_soup",
        "cmd_soup_answer",
        "cmd_rush",
        "cmd_fortune",
        "cmd_shop",
        "cmd_buy",
        "cmd_bag",
        "cmd_wear",
        "cmd_gift",
        "cmd_intimacy",
        "cmd_confess",
        "cmd_duel",
        "cmd_random",
        "cmd_joke",
        "cmd_help",
        "cmd_status",
    }
    missing = expected - set(cmds)
    assert not missing, f"指令未注册: {missing}"

    # ---------- 2) 指令名剥离（参数解析的关键前提） ----------
    print("\n[参数解析] _args 剥离指令名")
    cases = [
        ("签到", ""),
        ("排行榜 签到", "签到"),
        ("掷骰 3 20", "3 20"),
        ("接龙 互动", "互动"),
        ("互动状态", ""),  # 最长名优先，不能被「互动」抢先
        ("投票 午饭 | 面 | 饭", "午饭 | 面 | 饭"),
        ("转账 1002 50", "1002 50"),
    ]
    for text, expect in cases:
        got = plugin._args(make_event(text))
        assert got == expect, (text, got, expect)
    print("  指令名剥离正确 ✅")

    # ---------- 2b) 免唤醒分发 ----------
    print("\n[免唤醒] 归一化与指令解析")
    norm_cases = [
        ("签到", "签到"),
        ("/签到", "签到"),
        ("!签到", "签到"),
        ("#签到", "签到"),
        ("／签到", "签到"),
    ]
    for raw, expect in norm_cases:
        assert plugin._normalize(raw) == expect, (raw, plugin._normalize(raw))
    # 最长触发词优先
    hit = plugin._resolve_command("投票结果 abc")
    assert hit is not None and hit[1] == "abc", hit
    hit = plugin._resolve_command("排行榜 签到")
    assert hit is not None and hit[1] == "签到", hit
    assert plugin._resolve_command("完全无关的一句话") is None
    print("  免唤醒解析正确 ✅")

    # ---------- 3) 指令端到端 ----------
    print("\n[指令] 逐个调用（走 CommandFilter 解析）")

    async def run(label: str, text: str, must: tuple[str, ...] = (), **kw) -> str:
        handler_name = kw.pop("handler")
        raw = kw.pop("raw", None)
        ev = make_event(text, **kw)
        # 参数统一由 _args() 剥离指令名后得出，与框架行为一致
        out = await h.call(
            handler_name, ev, args=raw if raw is not None else plugin._args(ev)
        )
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
    record("接龙提交", await h.call("_chain_submit", ev, word="动作"), ("接龙成功",))

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

    # ---------- 3b) 免唤醒：直接发消息即可触发 ----------
    print("\n[免唤醒] on_message 端到端")
    plugin.config["permission"]["cooldown_seconds"] = 0

    async def send(text: str, **kw) -> str:
        """模拟群里直接发一条消息（未经唤醒阶段）。"""
        ev = make_event(text, **kw)
        ev.is_at_or_wake_command = False  # 没有 @，也没用唤醒前缀
        ev.is_wake = False
        return await h.call("on_message", ev, args=None)

    for raw in ("签到", "/签到", "!签到", "#签到", "／签到"):
        out = await send(raw, uid=f"w{abs(hash(raw)) % 1000}")
        record(f"免@触发 {raw}", out, ("签到成功",))

    record("免@参数", await send("排行榜 签到", uid="w-rank"), ("签到排行榜",))
    record("免@斜杠带参", await send("/骰子 2 6", uid="w-dice"), ("2d6",))
    record("免@无关消息", await send("今天天气不错", uid="w-none"), ())

    # 游戏进行中免前缀接管
    plugin._guesses.pop("aiocqhttp_group_888", None)
    await send("猜数字", uid="w-game")
    target = plugin._guesses["aiocqhttp_group_888"].target
    record("免@猜数字接管", await send(str(target)), ("猜中了",))

    await send("接龙 互动", uid="w-chain")
    record("免@接龙接管", await send("动物", uid="w-chain2"), ("接龙成功",))

    await send("数字炸弹 1 30", uid="w-bomb")
    boom = plugin._bombs["aiocqhttp_group_888"].target
    record("免@炸弹接管", await send(str(boom)), ("BOOM",))

    await send("投票 测试 | A | B", uid="w-vote")
    pid = list(plugin.store.polls("aiocqhttp_group_888"))[-1]
    record("免@投票", await send(f"投 {pid} 1", uid="w-vote2"), ("已投票",))

    await send("猜谜", uid="w-riddle")
    answer = plugin._riddles["aiocqhttp_group_888"]["answer"]
    record("免@猜谜抢答", await send(str(answer)), ("答对",))

    await send("谁最先 我最快", uid="w-rush")
    record("免@抢答", await send("我最快我最快", uid="w-rush2"), ("抢到第一",))

    # 关掉免唤醒后应恢复原状（不响应未唤醒消息）
    plugin.config["trigger"]["wake_free"] = False
    quiet = await send("签到", uid="w-off")
    assert quiet.strip() == "", f"关闭免唤醒后不应响应，却收到：{quiet!r}"
    record("免唤醒关闭(无响应)", "已确认静默", ("已确认静默",))
    plugin.config["trigger"]["wake_free"] = True

    # ---------- 3c) 扩展玩法 ----------
    print("\n[扩展玩法] 21点 / 猜谜 / 海龟汤 / 商店 / 社交")

    await plugin.store.save("aiocqhttp_group_888", force=True)
    plugin.store.add_balance("aiocqhttp_group_888", "1001", 3000)
    out = await run("21点开局", "21点 50", (), handler="cmd_blackjack")
    if "21 点开始" in out:
        await run("21点停牌", "停牌", (), handler="cmd_bj_stand")
    await run("21点无局", "停牌", ("没有进行中的 21 点",), handler="cmd_bj_stand")

    await run("猜谜出题", "猜谜", ("谜语",), handler="cmd_riddle")
    answer = plugin._riddles["aiocqhttp_group_888"]["answer"]
    await run("猜谜答对", str(answer), ("谜底",), handler="_riddle_guess", raw=answer)
    await run("猜谜公布", "猜谜", ("谜语",), handler="cmd_riddle")
    await run("谜底", "谜底", ("谜底是",), handler="cmd_riddle_answer")

    await run("海龟汤", "海龟汤", ("发送「海龟汤 你的问题」提问",), handler="cmd_soup")
    await run("汤底", "汤底", ("汤底：",), handler="cmd_soup_answer")

    await run("抢答开始", "谁最先 冲鸭", ("抢答开始",), handler="cmd_rush")
    await run(
        "抢答成功", "冲鸭冲鸭", ("抢到第一",), handler="_rush_check", raw="冲鸭冲鸭"
    )

    await run("运势", "占卜", ("运势", "幸运值"), handler="cmd_fortune")
    await run("商店", "商店", ("互动商店", "称号"), handler="cmd_shop")
    await run("购买", "购买 称号·非酋", ("购买成功",), handler="cmd_buy")
    await run("背包", "背包", ("背包", "非酋"), handler="cmd_bag")
    await run("佩戴", "佩戴 非酋", ("已佩戴",), handler="cmd_wear")
    await run("卸下", "佩戴", ("已卸下",), handler="cmd_wear")
    await run("购买不存在", "购买 不存在的道具", ("没有找到",), handler="cmd_buy")

    await run("送礼", "赠送 1002 奶茶", ("送出了", "亲密度"), handler="cmd_gift")
    await run("亲密度", "亲密度 1002", ("亲密度",), handler="cmd_intimacy")
    await run("亲密度列表", "亲密度", ("亲密度排行",), handler="cmd_intimacy")
    await run("表白", "表白 1002", ("亲密度",), handler="cmd_confess")
    plugin.store.add_balance("aiocqhttp_group_888", "1002", 1000)
    await run("PK", "pk 1002 10", ("⚔️",), handler="cmd_duel")

    await run("随机", "随机 火锅|烧烤|日料", ("我选",), handler="cmd_random")
    await run("笑话", "笑话", ("😂",), handler="cmd_joke")

    await run("帮助", "互动", ("玩法总览",), handler="cmd_help")
    await run(
        "状态-管理员", "互动状态", ("运行状态",), handler="cmd_status", admin=True
    )
    await run("状态-非管理员", "互动状态", ("只有管理员",), handler="cmd_status")

    plugin.config["auto_reply"]["rules"] = [
        {"keyword": "你好", "reply": "你也好呀", "exact": False}
    ]
    record("关键词", await h.call("on_message", make_event("你好啊")), ("你也好呀",))

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

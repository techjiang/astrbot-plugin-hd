"""指令签名契约回归。

AstrBot 的 ``CommandFilter`` 会把 handler 的每个形参都当成「指令参数」解析：
有默认值的参数可以省略，**没有默认值的一律视为必填**。缺参时它抛
``ValueError("必要参数缺失...")``，唤醒阶段捕获后会把这条指令整个跳过 ——
现象就是群里发什么都没反应。

``**kwargs`` 会在这里踩坑：``inspect.signature`` 把它展开成一个
``VAR_KEYWORD`` 参数，而 ``CommandFilter`` 只看 ``default is empty``，
于是 ``kwargs`` 被判成必填参数，任何指令都解析不了。

因此约定：**指令 handler 只允许有带默认值的位置参数**，不得用 ``**kwargs``。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 先导入 astrbot.api 完成星标模块初始化，否则直接导入 core.star 会因
# 循环导入而失败（与 tests/test_main.py 的做法保持一致）。
import astrbot.api.event  # noqa: E402,F401
from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    star_handlers_registry,
)
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402

MODULE_PATH = "astrbot_plugin_hudong.main"


def _command_filters():
    """列出本插件注册的全部指令过滤器及其 handler 名。"""
    found = []
    for handler in star_handlers_registry:
        if handler.handler_module_path != MODULE_PATH:
            continue
        for f in handler.event_filters:
            if isinstance(f, CommandFilter):
                found.append((handler.handler_name, f))
    return found


def test_has_registered_commands():
    """确保确实扫到了指令，避免注册表变动后本文件变成空跑。"""
    assert len(_command_filters()) >= 20


def test_no_var_keyword_in_command_signatures():
    """指令 handler 不得声明 ``**kwargs``，否则整条指令会被框架跳过。"""
    offenders = []
    for name, _ in _command_filters():
        fn = getattr(plugin_main.InteractionPlugin, name, None)
        if fn is None:
            continue
        for p in inspect.signature(fn).parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                offenders.append(f"{name}(**{p.name})")
    assert not offenders, f"指令不应使用 **kwargs：{offenders}"


@pytest.mark.parametrize("_name", [n for n, _ in _command_filters()])
def test_command_accepts_empty_args(_name):
    """每条指令在「无参数」时都必须能通过框架的参数解析。

    这是本次线上问题的直接回归：过去 20 条指令全部在这里抛出
    「必要参数缺失」，导致插件整体不可用。
    """
    filters = dict(_command_filters())
    cf = filters[_name]
    # 不修改原对象，避免污染其它用例
    probe = CommandFilter.__new__(CommandFilter)
    probe.handler_params = cf.handler_params
    try:
        probe.validate_and_convert_params([], cf.handler_params)
    except Exception as e:  # noqa: BLE001 - 失败原因要完整带出
        pytest.fail(f"指令 {_name} 无参数解析失败：{cf.print_types()} → {e}")


# --------------------------------------------------------------------- 分发键契约

# 分发键测试只需要「能取出 message_str」的极简事件替身，
# 不必构造真实 AstrMessageEvent —— `_args()` 只读这一个字段。
_FakeEvent = type(
    "_FakeEvent", (), {"__init__": lambda self, t: setattr(self, "message_str", t)}
)


@pytest.fixture
def plugin():
    """构造一个不落盘的插件实例（配置取空，走全部默认值）。"""
    return plugin_main.InteractionPlugin(context=None, config={})


class TestDispatchCoverage:
    """免唤醒分发表与指令名剥离表的同步契约。

    免唤醒入口会把消息归一化后查 ``_dispatch`` 表，命中则交给 handler；
    而 handler 内部一律用 ``_args(event)`` 自行剥离指令名，剥离依据是
    ``COMMAND_NAMES``。

    两处**必须保持同步**：只要 ``_dispatch`` 里挂了某个触发词，它就必须
    出现在 ``COMMAND_NAMES`` 里。否则：

    - 免唤醒命中没问题（dispatch 有它）；
    - 但 ``_args()`` 剥不掉它，于是 ``抛硬币 正 10`` 会把 ``抛硬币``
      当成第一个参数 —— 表现为「指令能触发，但参数永远解析不对」。

    这类 bug 极隐蔽（指令「有反应」，只是行为不对），因此用结构断言锁死：
    新增任何触发词时，漏加 ``COMMAND_NAMES`` 会直接让测试变红。
    """

    def test_every_dispatch_key_is_strippable(self, plugin):
        keys = set(plugin._dispatch)
        names = set(plugin_main.COMMAND_NAMES)
        missing = sorted(keys - names)
        assert not missing, (
            "以下免唤醒触发词没有出现在 COMMAND_NAMES 里，"
            f"会导致带参指令解析错误：{missing}"
        )

    def test_dispatch_keys_have_no_whitespace(self, plugin):
        """分发键不能含空白，否则永远匹配不上（归一化会把空白压成单空格）。"""
        bad = [k for k in plugin._dispatch if k != k.strip() or " " in k]
        assert not bad, f"分发键含空白: {bad}"

    def test_args_strips_every_dispatch_key(self, plugin):
        """每一个分发键都必须能被 ``_args`` 完整剥离。"""
        failed: list[str] = []
        for key in plugin._dispatch:
            event = _FakeEvent(f"{key} 测试参数")
            if plugin._args(event) != "测试参数":
                failed.append(key)
        assert not failed, f"以下触发词剥离失败: {failed}"

    def test_args_strips_bare_command_without_args(self, plugin):
        """不带参数时，剥离结果必须是空串（不能把指令名当参数）。"""
        failed = [k for k in plugin._dispatch if plugin._args(_FakeEvent(k)) != ""]
        assert not failed, f"以下触发词无参时未剥净: {failed}"

    def test_longest_key_wins_in_resolution(self, plugin):
        """匹配必须「最长优先」，否则「投票结果」会被「投票」抢走。"""
        for text, expect in (
            ("投票结果 abc", "abc"),
            ("互动状态", ""),
            ("我的加成", ""),
        ):
            hit = plugin._resolve_command(text)
            assert hit is not None, text
            assert hit[1] == expect, (text, hit[1], expect)


class TestRankAliasCoverage:
    """``_RANK_TITLES`` 与 ``_RANK_ALIASES`` 的同步契约。

    排行榜的展示文案取自 ``_RANK_TITLES``，而用户能输入的维度取自
    ``_RANK_ALIASES``。两者一旦脱节，就会出现两类静默 bug：

    - ``_RANK_ALIASES`` 映射到一个 ``_RANK_TITLES`` 里没有的字段 →
      ``top_users`` 取不到数据，永远回「还没有数据」；
    - 新增了 ``_RANK_TITLES`` 维度却忘了加别名 → 用户按提示输入却报「可排行维度」。

    两种都「不报错」，所以用结构断言锁死。
    """

    def test_every_alias_target_exists_in_titles(self):
        titles = set(plugin_main.InteractionPlugin._RANK_TITLES)
        targets = set(plugin_main.InteractionPlugin._RANK_ALIASES.values())
        missing = sorted(targets - titles)
        assert not missing, f"以下排行维度没有展示名（_RANK_TITLES 缺失）：{missing}"

    def test_every_title_is_reachable_by_some_alias(self):
        titles = set(plugin_main.InteractionPlugin._RANK_TITLES)
        targets = set(plugin_main.InteractionPlugin._RANK_ALIASES.values())
        unreachable = sorted(titles - targets)
        assert not unreachable, (
            f"以下排行维度没有任何输入别名，用户按提示输入也会报错：{unreachable}"
        )

    def test_titles_include_help_text_dimensions(self):
        """帮助文本里列出的维度必须都能真的解析出来。

        帮助文案是从 ``_RANK_TITLES`` 拼出来的，这里反向验证每个展示名
        至少能通过一个别名命中，避免「帮助里写了但用不了」。
        """
        plugin_aliases = plugin_main.InteractionPlugin._RANK_ALIASES
        for field_name, title in plugin_main.InteractionPlugin._RANK_TITLES.items():
            assert field_name in plugin_aliases.values(), title

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

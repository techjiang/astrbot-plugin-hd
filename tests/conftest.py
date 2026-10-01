"""pytest 配置：启用 asyncio 插件模式的兼容处理。"""

from __future__ import annotations

import asyncio

import pytest


def pytest_configure(config):
    """注册 asyncio 标记，避免未安装 pytest-asyncio 时报警。"""
    config.addinivalue_line("markers", "asyncio: 异步测试用例")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """内置的极简 asyncio 支持：``async def`` 测试自动在事件循环中运行。"""
    func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(func):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(func(**kwargs))
    return True

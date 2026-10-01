"""pytest 根配置。

AstrBot 会把插件目录作为包导入，``main.py`` 因此使用相对导入
（``from . import games``）。本文件在测试时把仓库根目录注册成一个
名为 ``astrbot_plugin_hudong`` 的包，使 ``import astrbot_plugin_hudong.main``
与线上行为保持一致。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = "astrbot_plugin_hudong"

if PKG not in sys.modules and ROOT not in sys.path:
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[PKG] = module
        spec.loader.exec_module(module)

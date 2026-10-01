"""互动 · AstrBot 群聊互动插件包入口。

AstrBot 会把插件目录本身作为 Python 包导入，因此这里显式声明包，
并导出主类与版本号，方便外部按包名引用。
"""

from __future__ import annotations

__version__ = "1.0.1"
__author__ = "科技酱"
__all__ = ["__version__", "__author__"]

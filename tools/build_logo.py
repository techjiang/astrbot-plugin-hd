"""从原始设计稿生成插件所需的全部图资源。

用法：
    python tools/build_logo.py <原始PNG> <输出目录>

例如：
    python tools/build_logo.py 设计稿.png assets

生成内容与用途见 ``tools/logo_tools.py`` 的模块文档字符串。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logo_tools import build_all  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    build_all(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"生成完毕 -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

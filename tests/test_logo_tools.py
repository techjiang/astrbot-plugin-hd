"""``tools/logo_tools.py`` 的单元测试。

这些测试保证 Logo 资源可以从设计稿确定性重建，并且不会把压在
六边形尖角上的「Astrbot」文字重新带回来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import logo_tools  # noqa: E402


@pytest.fixture
def design(tmp_path: Path) -> Path:
    """构造一张与真实设计稿同版式的合成图，避免测试依赖大文件。"""
    from PIL import ImageDraw

    size = 615
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    # 六边形：上下尖角 + 左右竖直段
    hexagon = [
        (313, 45),
        (523, 190),
        (523, 470),
        (313, 549),
        (91, 470),
        (91, 190),
    ]
    draw.polygon(hexagon, fill=(32, 32, 92, 255), outline=(240, 240, 240, 255))
    # 字标（橙红），压在尖角上方
    draw.rectangle((150, 337, 470, 420), fill=(240, 120, 30, 255))
    # 尖角之下、面板轮廓之外的文字（需要被裁掉）
    draw.rectangle((200, 500, 430, 540), fill=(11, 18, 63, 255))
    path = tmp_path / "design.png"
    img.save(path)
    return path


def test_clean_edges_removes_black(design: Path) -> None:
    raw = logo_tools.load_raw(design)
    cleaned = logo_tools.clean_edges(raw)
    assert cleaned.getpixel((300, 5))[3] == 0


def test_panel_mask_keeps_apex() -> None:
    mask = logo_tools.panel_mask((500, 560), (60, 20))
    # 中列在字标上方应当保留（尖角区域）
    mid = 313 - 60
    assert mask.getpixel((mid, 440)) == 255
    # 六边形两侧的斜边之外应当被清掉
    assert mask.getpixel((50, 440)) == 0
    # 字标以下整段都要清掉，避免带入 Astrbot 文字
    assert mask.getpixel((mid, 461)) == 0


def test_panel_mask_row_major_order() -> None:
    """putdata 是行优先的，列与行的映射不能写反。"""
    mask = logo_tools.panel_mask((500, 560), (60, 20))
    # 同一行内，两侧斜边之外被截断，中间列保留
    assert mask.getpixel((40, 440)) == 0
    assert mask.getpixel((250, 440)) == 255
    # 同一列内，靠上的行保留、靠下的行清空
    assert mask.getpixel((250, 300)) == 255
    assert mask.getpixel((250, 470)) == 0


def test_crop_panel_excludes_text_below(design: Path) -> None:
    panel = logo_tools.crop_panel(logo_tools.load_raw(design))
    px = panel.load()
    # 面板整体被裁短，且不再包含尖角下方那行深蓝文字 (11, 18, 63)
    assert panel.height < 615
    for y in range(panel.height):
        for x in range(panel.width):
            if px[x, y][3] > 0:
                assert px[x, y][:3] != (11, 18, 63)


def test_build_logo_is_square(design: Path) -> None:
    logo = logo_tools.build_logo(logo_tools.load_raw(design))
    assert logo.width == logo.height


def test_build_glyph_drops_wordmark(design: Path) -> None:
    raw = logo_tools.load_raw(design)
    logo = logo_tools.build_logo(raw)
    glyph = logo_tools.build_glyph(raw)
    assert glyph.height <= logo.height


def test_build_all_writes_expected_files(design: Path, tmp_path: Path) -> None:
    out = tmp_path / "assets"
    logo_tools.build_all(design, out)
    expected = {
        "logo.png",
        "logo@2x.png",
        "logo-512.png",
        "logo-256.png",
        "logo-128.png",
        "logo-64.png",
        "logo-32.png",
        "glyph.png",
        "glyph-256.png",
        "glyph-128.png",
        "glyph-64.png",
        "glyph-48.png",
        "glyph-32.png",
        "glyph-16.png",
        "icon.png",
        "favicon.png",
        "logo-full.png",
        "banner.png",
        "social-preview.png",
    }
    written = {p.name for p in out.iterdir()}
    assert expected <= written
    # 插件根目录也要有一份 logo.png，AstrBot 会硬编码读取
    assert (tmp_path / "logo.png").is_file()


def test_build_all_is_deterministic(design: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    logo_tools.build_all(design, first)
    logo_tools.build_all(design, second)
    for name in ("logo.png", "glyph-128.png", "banner.png"):
        assert (first / name).read_bytes() == (second / name).read_bytes()

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


def _hexagon_points() -> list[tuple[int, int]]:
    """按 ``logo_tools`` 的实测常量反推六边形顶点。

    这样合成图与真实设计稿共用同一套几何：一旦常量被改错
    （例如斜边斜率写成直角的），下面的断言就会失败。
    """
    left, right = logo_tools.LEFT_WALL_X, logo_tools.RIGHT_WALL_X
    return [
        (313, 31),  # 上尖角
        (
            right,
            int(logo_tools.TOP_RIGHT_SLOPE * right + logo_tools.TOP_RIGHT_INTERCEPT),
        ),
        (right, int(logo_tools.RIGHT_SLOPE * right + logo_tools.RIGHT_INTERCEPT)),
        (301, 536),  # 下尖角
        (left, int(logo_tools.LEFT_SLOPE * left + logo_tools.LEFT_INTERCEPT)),
        (left, int(logo_tools.TOP_LEFT_SLOPE * left + logo_tools.TOP_LEFT_INTERCEPT)),
    ]


@pytest.fixture
def design(tmp_path: Path) -> Path:
    """构造一张与真实设计稿同版式的合成图，避免测试依赖大文件。"""
    from PIL import ImageDraw

    size = 615
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    draw.polygon(
        _hexagon_points(), fill=(32, 32, 92, 255), outline=(240, 240, 240, 255)
    )
    # 字标（橙红），压在尖角上方，下沿与真实的 WORDMARK_BOTTOM 对齐
    draw.rectangle(
        (150, 337, 470, logo_tools.WORDMARK_BOTTOM - 1), fill=(240, 120, 30, 255)
    )
    # 尖角之下、面板轮廓之外的文字（需要被裁掉）
    draw.rectangle((200, 500, 430, 540), fill=(11, 18, 63, 255))
    # Astrbot 文字：恰好落在面板内部、字标之下，用来验证"不误伤面板内容"
    draw.rectangle(
        (200, logo_tools.WORDMARK_BOTTOM + 2, 430, 520), fill=(11, 18, 63, 255)
    )
    path = tmp_path / "design.png"
    img.save(path)
    return path


def test_clean_edges_removes_black(design: Path) -> None:
    raw = logo_tools.load_raw(design)
    cleaned = logo_tools.clean_edges(raw)
    assert cleaned.getpixel((300, 5))[3] == 0


def test_panel_mask_keeps_apex() -> None:
    """下尖角必须保住：左壁到尖角的底边严格递增，且尖角明显更深。"""
    mask = logo_tools.panel_mask((500, 560), (60, 20))

    def bottom(column: int) -> int:
        """返回原图列号 ``column`` 的底边（含 origin 偏移）。"""
        x = column - 60
        return max(y for y in range(560) if mask.getpixel((x, y)) == 255) + 20

    # 从左壁往尖角走，底边单调变深（原图坐标）
    assert bottom(121) < bottom(181) < bottom(241)
    # 尖角附近被字标拦平，宽度有限：右侧已开始回升
    assert bottom(241) > bottom(400)
    # 尖角比左壁处深得多（真正的 V 形，而不是被切平的梯形）
    assert bottom(301) - bottom(94) > 60
    # 尖角附近（原图 x=280、y=470）仍属面板
    assert mask.getpixel((280 - 60, 470 - 20)) == 255
    # 左侧斜边之外整体清空（原图 x=150、y=470 已在斜边下方）
    assert mask.getpixel((150 - 60, 470 - 20)) == 0


def test_panel_mask_cuts_below_wordmark() -> None:
    mask = logo_tools.panel_mask((500, 560), (60, 20))
    # 字标以下（原图 y >= WORDMARK_BOTTOM）整行都要清掉，
    # 避免把压在尖角之外的「Astrbot」文字带进 Logo
    for x in range(500):
        assert mask.getpixel((x, logo_tools.WORDMARK_BOTTOM - 20)) == 0


def test_panel_mask_row_major_order() -> None:
    """putdata 是行优先的，列与行的映射不能写反。"""
    mask = logo_tools.panel_mask((500, 560), (60, 20))
    # 同一行（原图 y=430）内：左侧斜边之外清空、中间保留
    assert mask.getpixel((30, 430 - 20)) == 0
    assert mask.getpixel((241, 430 - 20)) == 255
    # 同一列内：靠上的行保留、字标以下的行清空
    assert mask.getpixel((241, 300 - 20)) == 255
    assert mask.getpixel((241, logo_tools.WORDMARK_BOTTOM - 20)) == 0


def test_crop_panel_excludes_text_below(design: Path) -> None:
    panel = logo_tools.crop_panel(logo_tools.load_raw(design))
    px = panel.load()
    # 面板整体被裁短，且不再包含字标以下那行深蓝文字 (11, 18, 63)
    assert panel.height < 615
    for y in range(panel.height):
        for x in range(panel.width):
            if px[x, y][3] > 0:
                assert px[x, y][:3] != (11, 18, 63)


def test_crop_panel_keeps_wordmark_and_apex(design: Path) -> None:
    """字标不能被拦腰截断，下尖角也不能被切平。"""
    panel = logo_tools.crop_panel(logo_tools.load_raw(design))
    px = panel.load()
    w, h = panel.size

    def opaque(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and px[x, y][3] > 0

    # 底边轮廓应当呈"尖角"：中列的最低不透明行低于靠左的列
    # （尖角附近各列都被 WORDMARK_BOTTOM 截平，所以比较时避开这个平台区）
    def lowest(x: int) -> int:
        rows = [y for y in range(h) if opaque(x, y)]
        return max(rows) if rows else -1

    assert lowest(w // 2) >= lowest(w // 2 - 70)
    assert lowest(w // 2 - 70) > lowest(20)


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


def test_panel_bottom_follows_slopes() -> None:
    """底边随列号变化：左侧递增、右侧递减，且在竖直壁处达到最大。"""
    left_depths = [logo_tools.panel_bottom(x) for x in range(100, 290, 20)]
    right_depths = [logo_tools.panel_bottom(x) for x in range(320, 510, 20)]
    assert left_depths == sorted(left_depths)
    assert right_depths == sorted(right_depths, reverse=True)
    # 尖角处最深
    assert logo_tools.panel_bottom(301) > logo_tools.panel_bottom(150)

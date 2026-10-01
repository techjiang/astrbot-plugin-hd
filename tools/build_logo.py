"""从 Issue 提供的原始 Logo 生成插件用图资源。

原始 PNG 是一张 615×615 的方图：黑色底 + 六边形徽标 + "互动" 字标 +
"Astrbot / INTERACTIVE PLUGIN" 文字。本脚本负责：

1. 保留带文字的完整方形版（icon.png / logo.png 的素材）；
2. 抠出纯六边形徽标（去掉白色描边与外部杂色、去掉底部文字），
   作为 AstrBot WebUI 插件 Logo（logo.png，框架固定读取该文件名）；
3. 生成 AstrBot 插件市场用的图标 icon.png；
4. 生成文档用横幅 banner.png 与各尺寸 UI 缩略图。

用法：
    python tools/build_logo.py <原始PNG> <输出目录>
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# 原始图中各元素的像素边界（已实测）
HEX_BOX = (88, 44, 527, 542)  # 六边形徽标（含"互动"字标与基座条，不含 Astrbot 文字）
FULL_BOX = (88, 44, 527, 563)  # 完整 Logo（含 Astrbot / INTERACTIVE PLUGIN）
HEX_NO_TEXT_BOX = (88, 44, 527, 458)  # 仅六边形图形，切在"互动"字标上方的安全位置

# 白色描边之外的半透明杂色阈值
ALPHA_CUTOFF = 16
DARK_CUTOFF = 18


def load_raw(path: str | Path) -> Image.Image:
    """打开原始 PNG 并统一为 RGBA。"""
    return Image.open(path).convert("RGBA")


def clean_edges(im: Image.Image) -> Image.Image:
    """清掉近黑与近透明的边缘杂色，得到干净的透明底图。"""
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < ALPHA_CUTOFF or (
                r < DARK_CUTOFF and g < DARK_CUTOFF and b < DARK_CUTOFF
            ):
                px[x, y] = (0, 0, 0, 0)
    return im


def trim(im: Image.Image) -> Image.Image:
    """按非透明像素裁掉四周空白。"""
    box = im.getbbox()
    return im.crop(box) if box else im


def square(im: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """把图放进正方形画布，四周留一点透明边距。"""
    w, h = im.size
    side = int(max(w, h) * (1 + pad_ratio * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas


def resize(im: Image.Image, size: int) -> Image.Image:
    """等比缩放到指定边长。"""
    return im.resize((size, size), Image.LANCZOS)


def main() -> int:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    raw = load_raw(src)

    # 1) 完整 Logo（透明底，方形）
    full = square(trim(clean_edges(raw.crop(FULL_BOX))))
    full.save(out / "logo-full.png")

    # 2) 六边形徽标（含"互动"字标）—— 用作 WebUI 插件 Logo
    hexa = square(trim(clean_edges(raw.crop(HEX_BOX))))
    hexa.save(out / "logo.png")
    # AstrBot 会硬编码读取「插件根目录/logo.png」，因此额外在仓库根放一份
    hexa.save(out.parent / "logo.png")
    hexa.save(out / "logo@2x.png")
    for size in (512, 256, 128, 64, 32):
        resize(hexa, size).save(out / f"logo-{size}.png")

    # 3) 纯图形六边形（无文字）—— 用作 favicon / 小尺寸图标
    glyph = square(trim(clean_edges(raw.crop(HEX_NO_TEXT_BOX))))
    glyph.save(out / "glyph.png")
    for size in (256, 128, 64, 48, 32, 16):
        resize(glyph, size).save(out / f"glyph-{size}.png")
    # AstrBot 插件市场图标
    resize(glyph, 512).save(out / "icon.png")
    resize(glyph, 128).save(out / "favicon.png")

    # 4) 文档横幅：完整 Logo 居中放在深色渐变底上
    banner_w, banner_h = 1200, 400
    banner = Image.new("RGBA", (banner_w, banner_h), (0, 0, 0, 0))
    for y in range(banner_h):
        ratio = y / max(1, banner_h - 1)
        color = (
            int(18 + ratio * 16),
            int(20 + ratio * 14),
            int(58 + ratio * 22),
            255,
        )
        for x in range(banner_w):
            banner.putpixel((x, y), color)
    logo_h = 320
    logo = full.resize((int(full.width * logo_h / full.height), logo_h), Image.LANCZOS)
    banner.paste(
        logo, ((banner_w - logo.width) // 2, (banner_h - logo.height) // 2), logo
    )
    banner.convert("RGB").save(out / "banner.png")

    # 5) 社交预览图 1280×640
    og = Image.new("RGB", (1280, 640), (16, 18, 54))
    logo2 = full.resize((int(full.width * 520 / full.height), 520), Image.LANCZOS)
    og.paste(logo2, ((1280 - logo2.width) // 2, (640 - logo2.height) // 2), logo2)
    og.save(out / "social-preview.png")

    print(f"生成完毕 -> {out}")
    for f in sorted(out.iterdir()):
        print(" ", f.name, f.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Logo 资源生成的可测工具函数。

把抠图 / 清底 / 导出逻辑从 CLI 拆出来，便于单测，也便于以后换设计稿。

设计稿（615×615，来自 Issue 附件）的版式：

```
┌──────────────────────────────┐
│         ╱‾‾‾‾‾‾‾╲            │
│        ╱  机器人  ╲           │  ← 六边形面板（白色描边 + 蓝紫渐变）
│       │    ╱╲     │          │
│       │    互动    │          │  ← 字标，压在六边形下尖角上
│        ╲  ╱  ╲   ╱           │
└─────────╲╱────╲╱────────────┘
│          Astrbot             │  ← 需裁掉
│      INTERACTIVE PLUGIN      │
└──────────────────────────────┘
```

难点在于**六边形的下尖角与「互动」字标、以及更下方的「Astrbot」文字
在源图上相互重叠**：按固定高度硬截会把尖角切断、也会把字标拦腰截断；
用估算的斜边补边则会在小尺寸下露出接缝。

这里的做法是**由白色描边实测六条边的直线方程**，再逐列取
「左右斜边中较靠上的那个」作为底边：

* 斜边以内保留 —— 下尖角自然保住，且尖角上方的「互动」字标完整；
* 斜边以外清空 —— 压在尖角之外的像素（若有）一并去掉；
* 底边再与字标下沿取较小值 —— 只切掉字标以下的「Astrbot」文字。

``LEFT_*`` / ``RIGHT_*`` 等常量均由 ``assets/design-source.png`` 实测得出，
测试里用合成图验证同一套逻辑，因此换设计稿也只需重新实测这几个数。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# ---- 原图版式（615×615），以下数值均由设计稿的白色描边实测 ----

# 六边形下部两条斜边拟合出的直线方程 y = k*x + b
LEFT_SLOPE, LEFT_INTERCEPT = 0.71921, 319.54
RIGHT_SLOPE, RIGHT_INTERCEPT = -0.67316, 738.54
# 六边形上部两条斜边
TOP_LEFT_SLOPE, TOP_LEFT_INTERCEPT = -0.60328, 219.78
TOP_RIGHT_SLOPE, TOP_RIGHT_INTERCEPT = 0.65323, -173.59
# 左右竖直壁的 x 范围（白描边实测），之外即斜边
LEFT_WALL_X, RIGHT_WALL_X = 94, 520
# "互动"字标的下沿（含白描边）。字标以下属于 Astrbot 文字区，
# 由于这段文字正好压在六边形尖角之外，需要一并截掉。
WORDMARK_BOTTOM = 480
# 面板轮廓的搜索窗口：略大于六边形，避免把装饰星点算进来
SEARCH_BOX = (60, 20, 560, 580)
# 完整 Logo（含 Astrbot / INTERACTIVE PLUGIN），仅用于文档横幅与社交预览
FULL_BOX = (60, 20, 560, 580)

# 近黑阈值：把近黑像素抹成透明（构建期一次性使用）
DARK_CUTOFF = 18
# 极低不透明度的边缘杂色
ALPHA_CUTOFF = 16


def load_raw(path: str | Path) -> Image.Image:
    """打开设计稿并统一为 RGBA。"""
    return Image.open(path).convert("RGBA")


def clean_edges(im: Image.Image) -> Image.Image:
    """把近黑像素抹成透明，去掉黑底与边缘杂色。

    注意仅对 ``DARK_CUTOFF`` 以下的"接近纯黑"像素生效，
    面板自身的深色（如星空的 #10123a）不受影响。

    Args:
        im: 输入图像。

    Returns:
        处理后的图像（原地修改并返回）。
    """
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


def panel_bottom(column: int) -> float:
    """返回六边形面板在第 ``column`` 列的底边 y 坐标。

    左右竖直壁之间取两条下斜边的**较小值**（即靠上的那条）：
    这样在下尖角附近，底边由斜边决定，尖角不会被切平；
    而在竖直壁一侧，两条斜边早已落到画外，底边由斜边自然延伸得到。

    Args:
        column: 原图坐标系下的列号。

    Returns:
        该列的底边 y（可能超过原图高度，表示该列不受斜边限制）。
    """
    left = LEFT_SLOPE * column + LEFT_INTERCEPT
    right = RIGHT_SLOPE * column + RIGHT_INTERCEPT
    return min(left, right)


def panel_mask(
    size: tuple[int, int], origin: tuple[int, int] = (0, 0), margin: int = 2
) -> Image.Image:
    """生成六边形面板的 alpha 遮罩。

    六边形外描边为白色，沿两条下斜边可以直接测出轮廓：
    底边以上的像素保留，以下置 0。底边同时与
    ``WORDMARK_BOTTOM``（「互动」字标下沿）取较小值，
    确保字标以下的「Astrbot」文字不会带进 Logo。

    Args:
        size: 遮罩尺寸。
        origin: 遮罩左上角在原图坐标系中的位置，用于把局部列号换算成绝对列号。
        margin: 斜边外额外保留的像素，避免抗锯齿边缘被切硬。

    Returns:
        单通道 ``L`` 模式遮罩。
    """
    width, height = size
    ox, oy = origin
    cuts = []
    for x in range(width):
        column = x + ox
        cut = int(panel_bottom(column) + margin) - oy
        cut = min(cut, WORDMARK_BOTTOM - oy)
        cuts.append(max(0, min(height, cut)))

    # putdata 按行优先读取，因此外层是 y、内层是 x
    mask = Image.new("L", size, 0)
    mask.putdata(
        [255 if y < cuts[x] else 0 for y in range(height) for x in range(width)]
    )
    return mask


def trim_alpha(im: Image.Image) -> Image.Image:
    """按非透明像素裁掉四周空白。"""
    box = im.getbbox()
    return im.crop(box) if box else im


def pad_to_square(im: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """把图放进正方形透明画布，四周留一点边距。"""
    w, h = im.size
    side = max(1, int(max(w, h) * (1 + pad_ratio * 2)))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas


def crop_panel(raw: Image.Image) -> Image.Image:
    """抠出六边形面板（机器人图形 + 互动字标）。

    套用 ``panel_mask`` 的轮廓遮罩，斜边以外（含压在尖角外侧的
    「Astrbot」文字）会被一并排除。

    Args:
        raw: 设计稿。

    Returns:
        透明底的六边形面板，已按内容收紧。
    """
    window = raw.crop(SEARCH_BOX)
    window.putalpha(panel_mask(window.size, SEARCH_BOX[:2]))
    return trim_alpha(clean_edges(window))


def build_logo(raw: Image.Image) -> Image.Image:
    """生成正方形插件 Logo。"""
    return pad_to_square(crop_panel(raw))


def wordmark_top(logo: Image.Image, threshold: int = 40) -> int:
    """找出「互动」字标在 Logo 中的顶行。

    字标顶部是橙红色（红很高、绿中、蓝低），而面板是蓝紫色系，
    机器人光束虽然也是橙色，但集中在画面右上，因此只在中央取样。

    Args:
        logo: 完整 Logo（``build_logo`` 的输出）。
        threshold: 单行最少匹配像素数，用于过滤零散装饰。

    Returns:
        字标顶行的 y 坐标；未识别到时退回高度估算值。
    """
    px = logo.load()
    width, height = logo.size
    x0, x1 = int(width * 0.2), int(width * 0.8)
    for y in range(height):
        hits = 0
        for x in range(x0, x1):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            if r > 200 and 100 < g < 170 and b < 90:
                hits += 1
                if hits >= threshold:
                    return y
    return int(height * 0.74)


def build_glyph(raw: Image.Image) -> Image.Image:
    """生成纯图形六边形（去掉"互动"字标）——小尺寸下更干净。

    字标在小尺寸下会糊成一团，因此裁到字标上方，只保留机器人图形与星点装饰。

    Args:
        raw: 设计稿。

    Returns:
        纯图形六边形。
    """
    logo = build_logo(raw)
    cut = max(1, wordmark_top(logo) - 4)
    return pad_to_square(trim_alpha(logo.crop((0, 0, logo.width, cut))))


def resize(im: Image.Image, size: int) -> Image.Image:
    """等比缩放到指定边长。"""
    return im.resize((size, size), Image.LANCZOS)


def make_backdrop(width: int, height: int) -> Image.Image:
    """生成文档用深色渐变底。"""
    backdrop = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (int(18 + ratio * 16), int(20 + ratio * 14), int(58 + ratio * 22), 255)
        for x in range(width):
            backdrop.putpixel((x, y), color)
    return backdrop


def paste_centered(
    canvas: Image.Image, logo: Image.Image, target_h: int
) -> Image.Image:
    """把 Logo 按指定高度等比缩放后居中贴上。"""
    w = max(1, int(logo.width * target_h / logo.height))
    scaled = logo.resize((w, target_h), Image.LANCZOS)
    canvas.paste(
        scaled,
        ((canvas.width - scaled.width) // 2, (canvas.height - target_h) // 2),
        scaled,
    )
    return canvas


def build_all(src: str | Path, out: str | Path) -> list[Path]:
    """从设计稿生成全部图资源。"""
    src, out = Path(src), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    raw = load_raw(src)

    logo = build_logo(raw)
    logo.save(out / "logo.png")
    # AstrBot 硬编码读取「插件根目录/logo.png」，需额外在仓库根放一份
    logo.save(out.parent / "logo.png")
    logo.save(out / "logo@2x.png")
    for size in (512, 256, 128, 64, 32):
        resize(logo, size).save(out / f"logo-{size}.png")

    glyph = build_glyph(raw)
    glyph.save(out / "glyph.png")
    for size in (256, 128, 64, 48, 32, 16):
        resize(glyph, size).save(out / f"glyph-{size}.png")
    resize(glyph, 512).save(out / "icon.png")
    resize(glyph, 128).save(out / "favicon.png")

    full = pad_to_square(trim_alpha(clean_edges(raw.crop(FULL_BOX))))
    full.save(out / "logo-full.png")

    banner = make_backdrop(1200, 400)
    paste_centered(banner, full, 320)
    banner.convert("RGB").save(out / "banner.png")

    social = Image.new("RGB", (1280, 640), (16, 18, 54))
    paste_centered(social, full, 520)
    social.save(out / "social-preview.png")

    return sorted(out.iterdir())

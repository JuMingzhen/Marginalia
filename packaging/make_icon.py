"""生成应用图标。

图标是构建产物而不是二进制素材：写成脚本，改配色时重跑一遍即可，
也不用在仓库里存一个谁都不敢动的 .ico。

设计要在 16px 下还认得出来，所以只保留三个元素：深色底、浅色页面、一道黄色高亮。
细节（文字行、页边批注笔画）只在大尺寸下可见，小尺寸里自然糊成质感。

    python packaging/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SUPERSAMPLE = 4  # 先画大图再缩，得到抗锯齿
BASE = 256
CANVAS = BASE * SUPERSAMPLE

INK = (31, 36, 48, 255)  # 底色
PAPER = (247, 243, 232, 255)  # 页面
LINE = (150, 148, 140, 255)  # 正文行
HIGHLIGHT = (255, 213, 74, 255)  # 高亮
MARGIN_MARK = (232, 122, 74, 255)  # 页边批注笔画

#: .ico 里打包的尺寸。Windows 在不同位置分别取用：
#: 16 任务栏/资源管理器细节视图，32 桌面，48 大图标，256 平铺视图
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

OUTPUT_ICO = Path("marginalia/resources/icon.ico")
OUTPUT_PNG = Path("marginalia/resources/icon.png")


def _s(value: float) -> int:
    """把 256 基准的坐标放大到超采样画布。"""
    return round(value * SUPERSAMPLE)


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 底：圆角方形
    draw.rounded_rectangle(
        [_s(8), _s(8), _s(248), _s(248)],
        radius=_s(52),
        fill=INK,
    )

    # 页面。偏右留出左侧页边——Marginalia 就是「写在页边上的话」
    page_left, page_top, page_right, page_bottom = _s(84), _s(40), _s(216), _s(216)
    draw.rounded_rectangle(
        [page_left, page_top, page_right, page_bottom],
        radius=_s(10),
        fill=PAPER,
    )

    # 正文行
    line_x0, line_x1 = _s(100), _s(200)
    line_height = _s(7)
    for index, y in enumerate(range(_s(66), _s(200), _s(24))):
        # 最后一行短一截，像段落末尾
        x1 = line_x1 - (_s(34) if index == 4 else 0)
        draw.rounded_rectangle(
            [line_x0, y, x1, y + line_height],
            radius=line_height // 2,
            fill=LINE,
        )

    # 高亮：盖住第二行。整个图标最醒目的元素，16px 下也看得见
    highlight_y = _s(66) + _s(24)
    draw.rounded_rectangle(
        [line_x0 - _s(5), highlight_y - _s(7), line_x1 - _s(12), highlight_y + line_height + _s(7)],
        radius=_s(6),
        fill=HIGHLIGHT,
    )

    # 页边批注：左侧一道竖笔画加一个小勾
    draw.rounded_rectangle(
        [_s(60), highlight_y - _s(10), _s(68), highlight_y + _s(30)],
        radius=_s(4),
        fill=MARGIN_MARK,
    )
    draw.line(
        [(_s(60), _s(160)), (_s(68), _s(176)), (_s(60), _s(192))],
        fill=MARGIN_MARK,
        width=_s(7),
        joint="curve",
    )

    return image.resize((BASE, BASE), Image.LANCZOS)


def main() -> None:
    icon = draw_icon()

    for path in (OUTPUT_ICO, OUTPUT_PNG):
        path.parent.mkdir(parents=True, exist_ok=True)

    icon.save(OUTPUT_PNG, format="PNG")
    icon.save(OUTPUT_ICO, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"已生成 {OUTPUT_ICO}（{', '.join(str(s) for s in ICO_SIZES)}）")
    print(f"已生成 {OUTPUT_PNG}")


if __name__ == "__main__":
    main()

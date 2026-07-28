"""高亮与选区的颜色。

笔记里存的是颜色**名字**（yellow / green / …），不是色值。这样以后调整色板不会
影响已经存下来的笔记，夜间模式也能给同一个名字换一套画法。

亮色主题下用**正片叠底**画高亮：结果 = 页面像素 × 色调，和真实荧光笔一样——
纸变黄了，字还是黑的。直接盖一层半透明色会把黑字也冲淡。

夜间主题不能用正片叠底：底色本来就暗，再乘一次就全黑了。改用低透明度直接叠加，
让高亮处微微发亮。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPainter

#: 高亮基色，按名字索引
HIGHLIGHT_BASE: dict[str, str] = {
    "yellow": "#ffd54a",
    "green": "#8fd98f",
    "blue": "#8ec7f0",
    "pink": "#f5a3bd",
    "purple": "#c3a6e8",
}

#: 正片叠底用的色调：基色往白里提，避免把整页压得太重
_TINT_LIGHTEN = 0.45

#: 夜间模式下直接叠加的透明度
_NIGHT_ALPHA = 64

SELECTION_COLOR = QColor(64, 132, 232, 90)
FLASH_COLOR = QColor(255, 160, 40)

#: 框选时的橡皮筋
REGION_STROKE = QColor(64, 132, 232)
REGION_FILL = QColor(64, 132, 232, 38)

#: 区域笔记画成描边框而不是色块——色块盖在扫描图上会把内容糊住
REGION_NOTE_FILL_ALPHA = 34
REGION_NOTE_STROKE_WIDTH = 2.0


def _lighten(color: QColor, amount: float) -> QColor:
    return QColor(
        round(color.red() + (255 - color.red()) * amount),
        round(color.green() + (255 - color.green()) * amount),
        round(color.blue() + (255 - color.blue()) * amount),
    )


def base_color(key: str) -> QColor:
    """色板上显示的纯色。"""
    return QColor(HIGHLIGHT_BASE.get(key, HIGHLIGHT_BASE["yellow"]))


def setup_highlight_painter(painter: QPainter, key: str, theme_key: str) -> QColor:
    """设好合成模式，返回该用的颜色。"""
    color = base_color(key)
    if theme_key == "night":
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        color.setAlpha(_NIGHT_ALPHA)
        return color
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    return _lighten(color, _TINT_LIGHTEN)

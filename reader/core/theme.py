"""阅读配色。

三档：原色 / 纸色 / 夜间。两种处理方式：

**纸色（tone）** —— 把 0..255 的灰阶线性重映射到一对「墨色 → 纸色」端点上。
逐通道做，白底变成暖纸色、黑字变成深褐，彩色内容按比例被同一条曲线拉过去，
效果是整体偏暖而不是变色。

**夜间（night）** —— 只反转**亮度**，保留色相与饱和度。

    逐通道取反（255 - x）是最容易想到的做法，但它会把插图变成负片：蓝色方框
    会变成橙色。正确的做法是在 HSL 里把 L 换成 1-L 而保持 H、S 不变，而这件事
    有一个恰好等价的整数写法——给三个通道同时加上 k = 255 - (max + min)。

    因为 L = (max+min)/2，三通道同加 k 会让 (max+min) 变成 (max+min) + 2k，
    取 k = 255 - (max+min) 就得到 510 - (max+min)，正是 L 的反转；而 max-min
    （即色度）完全不变，所以色相和饱和度原样保留。

    附带的好处是结果天然落在 [0,255] 内，不需要裁剪：
    out_max = max + k = 255 - min ≤ 255，out_min = min + k = 255 - max ≥ 0。

    反转之后再把整体压缩到 [FLOOR, CEIL]，让纯白不至于变成死黑、纯黑不至于变成
    刺眼的纯白。这一步是三通道同比例的线性变换，同样不影响色相。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

# (r, g, b)
Rgb = tuple[int, int, int]

Mode = Literal["none", "tone", "night"]

#: 夜间模式反转后压缩到的亮度区间
NIGHT_FLOOR = 22
NIGHT_CEIL = 214


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    mode: Mode
    view_bg: str  # 页面之外的画布底色
    page_border: str
    page_bg: str  # 占位图底色（该主题下「纸」的颜色）
    page_fg: str  # 占位图上页码的颜色
    ink: Rgb = (0, 0, 0)  # mode == "tone" 时：原图纯黑映射到的颜色
    paper: Rgb = (255, 255, 255)  # mode == "tone" 时：原图纯白映射到的颜色


THEMES: dict[str, Theme] = {
    "normal": Theme(
        key="normal",
        label="原色",
        mode="none",
        view_bg="#3c3f41",
        page_border="#2b2b2b",
        page_bg="#ffffff",
        page_fg="#9a9a9a",
    ),
    "sepia": Theme(
        key="sepia",
        label="纸色",
        mode="tone",
        ink=(58, 50, 38),
        paper=(244, 236, 216),
        view_bg="#5c5348",
        page_border="#4a4238",
        page_bg="#f4ecd8",
        page_fg="#9c9078",
    ),
    "night": Theme(
        key="night",
        label="夜间",
        mode="night",
        view_bg="#161618",
        page_border="#0d0d0f",
        page_bg="#18181a",
        page_fg="#6a6a70",
    ),
}

THEME_ORDER = ["normal", "sepia", "night"]
DEFAULT_THEME = "normal"


def get(key: str) -> Theme:
    return THEMES.get(key, THEMES[DEFAULT_THEME])


def next_theme(key: str) -> str:
    try:
        index = THEME_ORDER.index(key)
    except ValueError:
        return DEFAULT_THEME
    return THEME_ORDER[(index + 1) % len(THEME_ORDER)]


# ----------------------------------------------------------------------
# 查找表
# ----------------------------------------------------------------------


def _tone_lut(theme: Theme) -> np.ndarray:
    """(256, 3)：把灰阶线性映射到 ink → paper。"""
    ramp = (np.arange(256, dtype=np.float32) / 255.0)[:, None]
    ink = np.array(theme.ink, dtype=np.float32)
    paper = np.array(theme.paper, dtype=np.float32)
    return np.clip(ink + (paper - ink) * ramp, 0, 255).astype(np.uint8)


def _night_lut() -> np.ndarray:
    """(256,)：亮度反转之后的区间压缩。"""
    ramp = np.arange(256, dtype=np.float32) / 255.0
    return np.clip(NIGHT_FLOOR + (NIGHT_CEIL - NIGHT_FLOOR) * ramp, 0, 255).astype(np.uint8)


_LUT_CACHE: dict[str, np.ndarray] = {}


def _lut(key: str) -> np.ndarray:
    if key not in _LUT_CACHE:
        theme = get(key)
        _LUT_CACHE[key] = _night_lut() if theme.mode == "night" else _tone_lut(theme)
    return _LUT_CACHE[key]


# ----------------------------------------------------------------------
# 应用
# ----------------------------------------------------------------------


def apply(rgb: np.ndarray, key: str) -> np.ndarray:
    """对 (h, w, 3) uint8 图像做配色处理，返回新数组（原色主题原样返回）。"""
    theme = get(key)
    if theme.mode == "none":
        return rgb
    if theme.mode == "night":
        return _apply_night(rgb, _lut(key))

    lut = _lut(key)
    out = np.empty_like(rgb)
    for channel in range(3):
        out[..., channel] = lut[:, channel][rgb[..., channel]]
    return out


def _apply_night(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    wide = rgb.astype(np.int16)
    # k = 255 - (max + min)，三通道同加；见模块文档
    shift = 255 - (wide.max(axis=2) + wide.min(axis=2))
    inverted = (wide + shift[..., None]).astype(np.uint8)
    return lut[inverted]

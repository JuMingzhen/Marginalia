"""配色的色彩数学。

夜间模式最容易写错：逐通道取反（255-x）会把插图变成负片——蓝色方框变成橙色。
这里把「色相必须保住」钉成测试。
"""

from __future__ import annotations

import colorsys

import numpy as np
import pytest

from marginalia.core import theme


def _apply(rgb: tuple[int, int, int], key: str) -> tuple[int, int, int]:
    arr = np.array([[list(rgb)]], dtype=np.uint8)
    out = theme.apply(arr, key)
    return tuple(int(v) for v in out[0][0])


def _hue(rgb: tuple[int, int, int]) -> float:
    h, _, _ = colorsys.rgb_to_hsv(*(v / 255 for v in rgb))
    return h


def _lightness(rgb: tuple[int, int, int]) -> float:
    return (max(rgb) + min(rgb)) / 2 / 255


def test_normal_is_identity():
    arr = np.array([[[12, 200, 77]]], dtype=np.uint8)
    assert theme.apply(arr, "normal") is arr


@pytest.mark.parametrize(
    "color",
    [(51, 102, 204), (220, 40, 40), (0, 0, 139), (240, 180, 20), (30, 160, 90)],
)
def test_night_preserves_hue(color):
    """夜间模式只翻转亮度，色相必须原样保留。"""
    out = _apply(color, "night")
    assert _hue(out) == pytest.approx(_hue(color), abs=0.01)


def test_night_inverts_lightness():
    assert _lightness(_apply((255, 255, 255), "night")) < 0.15  # 白底 → 暗
    assert _lightness(_apply((0, 0, 0), "night")) > 0.75  # 黑字 → 亮


def test_night_stays_in_range():
    """亮度反转的构造保证不会溢出，全灰阶扫一遍确认。"""
    ramp = np.arange(256, dtype=np.uint8)
    grid = np.stack(np.meshgrid(ramp, ramp, indexing="ij"), axis=-1)
    rgb = np.concatenate([grid, grid[..., :1]], axis=-1).astype(np.uint8)
    out = theme.apply(rgb, "night")
    assert out.dtype == np.uint8
    assert out.min() >= theme.NIGHT_FLOOR - 1 and out.max() <= theme.NIGHT_CEIL + 1


def test_sepia_maps_endpoints():
    assert _apply((255, 255, 255), "sepia") == theme.get("sepia").paper
    assert _apply((0, 0, 0), "sepia") == theme.get("sepia").ink


def test_sepia_keeps_blue_blue():
    """纸色是暖化不是变色：蓝色依然是蓝色。"""
    out = _apply((51, 102, 204), "sepia")
    assert out[2] > out[1] > out[0]


def test_theme_cycle_wraps():
    key = theme.DEFAULT_THEME
    seen = [key]
    for _ in range(len(theme.THEME_ORDER)):
        key = theme.next_theme(key)
        seen.append(key)
    assert seen[0] == seen[-1]
    assert set(seen) == set(theme.THEME_ORDER)


def test_unknown_theme_falls_back():
    assert theme.get("不存在的主题").key == theme.DEFAULT_THEME
    assert theme.next_theme("不存在的主题") == theme.DEFAULT_THEME

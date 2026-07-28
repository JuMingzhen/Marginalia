"""画布的布局算术与渲染管线。

重点验证两件事：虚拟化确实只碰视口附近的页，以及 (页码, 页内比例) 能精确往返
——后者是阅读进度和将来的笔记锚点共同依赖的基础。
"""

from __future__ import annotations

import itertools

import pytest

from marginalia.core.document import Document
from marginalia.core.render import PageRenderer
from marginalia.ui.page_view import BASE_SCALE, PageView, ZoomMode


@pytest.fixture
def view(qapp, sample_pdf):
    doc = Document(sample_pdf)
    renderer = PageRenderer(sample_pdf, cache_mb=32)
    widget = PageView()
    widget.resize(900, 700)
    widget.set_document(doc, renderer)
    yield widget
    renderer.shutdown()
    doc.close()


def test_layout_is_monotonic(view):
    tops = view._page_tops
    assert len(tops) == 20
    assert all(b > a for a, b in itertools.pairwise(tops))


def test_content_height_covers_every_page(view):
    doc = view.document
    expected = (
        2 * PageView.MARGIN
        + sum(doc.page_size(i)[1] * view._scale for i in range(doc.page_count))
        + PageView.GAP * (doc.page_count - 1)
    )
    assert view._content_h == pytest.approx(expected)


def test_fit_width_fills_viewport(view):
    view.set_zoom_mode(ZoomMode.FIT_WIDTH)
    page_width = view.document.max_page_size()[0] * view._scale
    assert page_width == pytest.approx(view.viewport().width() - 2 * PageView.MARGIN)


def test_fit_page_fits_both_axes(view):
    view.set_zoom_mode(ZoomMode.FIT_PAGE)
    max_w, max_h = view.document.max_page_size()
    assert max_w * view._scale <= view.viewport().width() - 2 * PageView.MARGIN + 1
    assert max_h * view._scale <= view.viewport().height() - 2 * PageView.MARGIN + 1


def test_custom_zoom_is_relative_to_100_percent(view):
    view.set_zoom(1.5)
    assert view.zoom == pytest.approx(1.5)
    assert view._scale == pytest.approx(1.5 * BASE_SCALE)


@pytest.mark.parametrize(("page", "ratio"), [(0, 0.0), (7, 0.25), (13, 0.5), (17, 0.6)])
def test_position_roundtrip(view, page, ratio):
    view.goto_page(page, ratio)
    got_page, got_ratio = view.current_position()
    assert got_page == page
    assert got_ratio == pytest.approx(ratio, abs=0.01)


def test_position_near_end_clamps_but_stays_put(view):
    """最后一页滚不到 75% 处——文档到底了，滚动条就此打住。

    读回来的比例因此比请求的小。这没问题，但它必须**可重放**：拿读回来的值再跳一次
    要落在同一处，否则每次开合书都会往回退一点。
    """
    view.goto_page(19, 0.75)
    landed = view.current_position()
    assert landed[0] == 19
    assert landed[1] < 0.75

    view.goto_page(*landed)
    assert view.current_position() == landed


def test_position_survives_zoom_change(view):
    view.goto_page(11, 0.4)
    view.set_zoom(2.4)
    page, ratio = view.current_position()
    assert page == 11
    assert ratio == pytest.approx(0.4, abs=0.02)


def test_visible_range_is_a_small_window(view):
    """虚拟化的核心：600 页的书也只应该碰到视口里的那几页。"""
    view.set_zoom_mode(ZoomMode.FIT_WIDTH)
    view.goto_page(10)
    first, last = view._visible_range()
    assert first == 10
    assert last - first < 5


def test_visible_range_clamps_at_both_ends(view):
    view.goto_page(0)
    first, _ = view._visible_range()
    assert first == 0

    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    _, last = view._visible_range()
    assert last == view.document.page_count - 1


def test_goto_page_clamps_out_of_range(view):
    view.goto_page(9999)
    assert view.current_position()[0] == view.document.page_count - 1
    view.goto_page(-5)
    assert view.current_position()[0] == 0


def test_renderer_returns_image_asynchronously(view, pump):
    renderer = view._renderer
    assert renderer.image_or_request(3, 2.0, "normal") is None  # 首次请求：未命中

    pump(1.5)

    image = renderer.image_or_request(3, 2.0, "normal")
    assert image is not None
    width, height = view.document.page_size(3)
    assert image.width() == pytest.approx(width * 2.0, abs=2)
    assert image.height() == pytest.approx(height * 2.0, abs=2)


def test_render_cache_is_reused(view, pump):
    renderer = view._renderer
    renderer.image_or_request(5, 1.5, "normal")
    pump(1.5)
    first = renderer.image_or_request(5, 1.5, "normal")
    second = renderer.image_or_request(5, 1.5, "normal")
    assert first is not None and second is not None
    assert first is second  # 同一个对象，没有重新渲染


def test_invalidate_lets_requests_be_resubmitted(view, pump):
    """作废在途请求后，同一页必须还能被重新请求到——否则会永远停在占位图。"""
    renderer = view._renderer
    renderer.image_or_request(8, 1.5, "normal")
    renderer.invalidate()
    pump(0.3)

    renderer.image_or_request(8, 1.5, "normal")
    pump(1.5)
    assert renderer.image_or_request(8, 1.5, "normal") is not None


def test_theme_switch_rerenders(view, pump):
    renderer = view._renderer
    renderer.image_or_request(2, 1.5, "normal")
    pump(1.5)
    assert renderer.image_or_request(2, 1.5, "normal") is not None

    view.set_theme("night")
    assert renderer.image_or_request(2, 1.5, "night") is None  # 缓存已清，需要重画
    pump(1.5)
    assert renderer.image_or_request(2, 1.5, "night") is not None


def test_empty_view_does_not_crash(qapp):
    widget = PageView()
    widget.resize(400, 300)
    widget.set_document(None, None)
    assert widget.current_position() == (0, 0.0)
    assert widget.visible_page() == 0
    widget.goto_page(3)  # 无文档时应当安静地什么都不做

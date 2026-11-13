import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/dashboard.html",
        "/detail.html?ticker=DEMOA",
        "/strategy.html",
        "/overview.html",
    ],
)
def test_mobile_pages_have_no_horizontal_scroll(app_url, page_factory, path):
    page = page_factory(width=390, height=800)
    page.goto(app_url + path)
    page.locator("body").wait_for()
    page.wait_for_timeout(500)
    scroll_width, inner_width = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    assert scroll_width <= inner_width + 1, f"{path}: {scroll_width}px > {inner_width}px"

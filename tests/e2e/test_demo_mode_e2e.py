"""E2E coverage for the synthetic demo-mode UI."""

import pytest


@pytest.mark.parametrize(
    ("path", "disabled_label"),
    [
        ("/", "更新股價"),
        ("/dashboard.html", None),
        ("/detail.html?ticker=DEMOA", "執行策略檢查"),
        ("/strategy.html", None),
        ("/overview.html", "執行批次檢查"),
    ],
)
def test_demo_banner_and_disabled_controls(app_url, page_factory, path, disabled_label):
    page = page_factory()
    page.goto(app_url + path)
    banner = page.locator(".demo-banner")
    banner.wait_for()
    assert "示範模式" in banner.inner_text()
    assert "合成資料" in banner.inner_text()
    if disabled_label:
        button = page.get_by_role("button", name=disabled_label, exact=False).first
        assert button.is_disabled()
        assert "示範模式" in (button.get_attribute("title") or "")


def test_demo_index_has_cards_and_add_is_disabled(app_url, page_factory):
    page = page_factory()
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    assert page.locator("#grid .card").count() == 5
    assert page.locator("#addTickerBtn").is_disabled()

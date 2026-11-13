import pytest


@pytest.mark.parametrize("width", [1100, 390])
def test_chart_indicator_toggles_persist(app_url, page_factory, width):
    page = page_factory(width=width)
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    panel = page.locator("#priceChartPanel")
    panel.wait_for()
    for label in ("MA10", "MA20", "MA50", "MA200", "成交量"):
        assert label in panel.inner_text()

    assert panel.get_by_role("button", name="MA10", exact=True).get_attribute("aria-pressed") == "false"
    for label in ("MA20", "MA50", "MA200", "成交量"):
        assert panel.get_by_role("button", name=label, exact=True).get_attribute("aria-pressed") == "true"

    ma10 = panel.get_by_role("button", name="MA10", exact=True)
    volume = panel.get_by_role("button", name="成交量", exact=False)
    ma10.click()
    volume.click()
    assert ma10.get_attribute("aria-pressed") == "true"
    assert volume.get_attribute("aria-pressed") == "false"
    page.reload()
    assert panel.get_by_role("button", name="MA10", exact=True).get_attribute("aria-pressed") == "true"
    assert panel.get_by_role("button", name="成交量", exact=False).get_attribute("aria-pressed") == "false"


@pytest.mark.parametrize("width", [1100, 390])
def test_strategy_result_separates_entry_from_now(app_url, page_factory, width):
    page = page_factory(width=width)
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    panel = page.locator("#strategyCheckPanel")
    panel.wait_for()
    page.wait_for_function("() => document.querySelector('#strategyCheckPanel')?.innerText.includes('買入當時')")
    text = panel.inner_text()
    assert "買入當時" in text
    assert "現在" in text
    assert "2025-06-30" in text and "買入" in text
    assert "截至今日" in text
    assert text.count("買入當時") >= 2

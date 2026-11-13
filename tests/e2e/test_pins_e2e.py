"""E2E coverage for pin management and detail-page pin results."""


def _pin_row(page, text):
    """The pin text sits in an <input> value (invisible to has_text), so resolve the row through its data-pin-id."""
    page.wait_for_function(
        "text => [...document.querySelectorAll('.pin-row')].some(r => [...r.querySelectorAll('input')].some(i => i.value === text))",
        arg=text,
    )
    pin_id = page.evaluate(
        "text => [...document.querySelectorAll('.pin-row')].find(r => [...r.querySelectorAll('input')].some(i => i.value === text)).dataset.pinId",
        text,
    )
    return page.locator(f'.pin-row[data-pin-id="{pin_id}"]')


def test_pins_add_compile_confirm_reorder_and_escape_hostile_text(
    app_url, page_factory, assert_no_page_errors
):
    page = page_factory()
    assert_no_page_errors(page)
    page.goto(app_url + "/strategy.html")
    page.locator("#pinnedRulesPanel").wait_for()
    initial_count = page.locator(".pin-row").count()

    text = "停損不超過6%"
    page.locator("#pinText").fill(text)
    page.locator("#pinCreateForm").get_by_role("button", name="新增").click()
    row = _pin_row(page, text)
    row.wait_for()
    assert page.locator(".pin-row").count() == initial_count + 1

    row.get_by_role("button", name="編譯").click()
    row.locator("button:has-text('確認這個解讀')").wait_for()
    assert "TECH.stop_pct" in row.inner_text()
    assert "本地範本" in row.inner_text()
    row.locator("button:has-text('確認這個解讀')").click()
    row.get_by_text("已確認").first.wait_for()
    assert "已確認" in row.inner_text()

    before = page.evaluate("fetch('/api/pins').then(r => r.json()).then(d => d.pins.map(p => p.id))")
    row.get_by_role("button", name="上移").click()
    page.wait_for_function(
        "before => fetch('/api/pins').then(r => r.json()).then(d => d.pins.map(p => p.id).join(',') !== before.join(','))",
        arg=before,
    )

    hostile = "<img src=x onerror=window.__pinXss=1>"
    page.locator("#pinText").fill(hostile)
    page.locator("#pinCreateForm").get_by_role("button", name="新增").click()
    _pin_row(page, hostile).wait_for()
    assert page.evaluate("window.__pinXss") is None
    assert page.locator("img[src='x']").count() == 0


def test_detail_page_shows_pins_panel(app_url, page_factory):
    page = page_factory()
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    panel = page.locator("#pinnedChecksPanel")
    panel.wait_for()
    page.wait_for_function("() => /通過|未知/.test(document.querySelector('#pinnedChecksPanel')?.innerText || '')")
    text = panel.inner_text()
    assert "固定檢查項目" in text
    assert "不經過 AI" in text
    assert any(status in text for status in ("通過", "未通過", "未知"))

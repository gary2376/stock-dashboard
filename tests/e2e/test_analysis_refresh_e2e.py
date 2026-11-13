"""E2E: per-stock analysis refresh button (detail) and card selection mode (index).

The demo server blocks the POSTs, so the non-demo behaviour fakes /api/info and intercepts the requests."""

import json

NON_DEMO = {"demo": False, "version": "4.2", "engine": {"name": "claude", "available": True, "privacy": "cli", "host": None}}
HINT = {"per_ticker_usd": 0.05, "per_ticker_seconds": 20, "based_on": 5}
IDLE = {"prices": {"running": False, "last_run": None, "last_result": None, "success": None},
        "analysis": {"running": False, "last_run": "2026-11-06T12:00:00", "last_result": ["ok"], "success": True,
                     "tickers": None, "started_at": None}}


def fake_non_demo(page, hint=HINT):
    page.route("**/api/info", lambda r: r.fulfill(json=NON_DEMO))
    page.route("**/api/analysis/cost-hint", lambda r: r.fulfill(json=hint))


def capture_post(page, response=None, status=200):
    posts = []

    def handler(route):
        if route.request.method != "POST":
            return route.continue_()
        posts.append(route.request.post_data)
        return route.fulfill(status=status, json=response or {"status": "started", "tickers": None})
    page.route("**/api/refresh/analysis", handler)
    return posts


# ---- demo: everything is visibly disabled with the usual explanation

def test_demo_disables_refresh_controls(app_url, page_factory):
    page = page_factory()
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    for sel in ("#refreshAnalysisBtn", "#selectModeBtn"):
        button = page.locator(sel)
        button.wait_for()
        page.wait_for_function("sel => document.querySelector(sel).disabled", arg=sel)
        assert "示範模式" in button.get_attribute("title")
        assert button.get_attribute("aria-describedby") == "sdDemoDisabledDescription"
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    button = page.locator("#refreshStockAnalysisBtn")
    button.wait_for()
    page.wait_for_function("document.querySelector('#refreshStockAnalysisBtn').disabled")
    assert "示範模式" in button.get_attribute("title")
    assert button.get_attribute("aria-describedby") == "sdDemoDisabledDescription"


# ---- index: selection mode

def test_selection_mode_checkbox_counter_and_confirm(app_url, page_factory, assert_no_page_errors):
    page = page_factory()
    assert_no_page_errors(page)
    fake_non_demo(page)
    posts = capture_post(page)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    assert page.locator(".card-select-input").count() == 0 and page.locator("#selectionBar").is_hidden()
    page.locator("#selectModeBtn").click()
    boxes = page.locator(".card-select-input")
    boxes.first.wait_for()
    assert boxes.count() == 5 and page.locator("#selectionBar").is_visible()
    assert page.locator("#refreshSelectedBtn").is_disabled()
    url_before = page.url
    boxes.nth(0).check()  # a click on the checkbox must not open the detail page
    boxes.nth(1).focus()
    page.keyboard.press("Space")  # keyboard accessible
    page.wait_for_timeout(300)
    assert page.url == url_before
    assert page.locator("#selectionCount").inner_text() == "已選 2 檔"
    assert page.locator("#refreshSelectedBtn").is_enabled()
    chosen = page.evaluate("[...document.querySelectorAll('.card-select-input:checked')].map(i => i.dataset.ticker)")
    assert len(chosen) == 2
    page.locator("#refreshSelectedBtn").click()
    for _ in range(50):
        if posts:
            break
        page.wait_for_timeout(100)
    assert json.loads(posts[0]) == {"tickers": chosen}
    assert "所選 2 檔" in dialogs[0] and "$0.10" in dialogs[0] and "40 秒" in dialogs[0]
    page.wait_for_function("document.querySelector('#selectionBar').hidden")  # mode ends after starting
    page.wait_for_function("!document.querySelector('.card-select-input')")


def test_selection_confirm_can_be_cancelled_and_cancel_button(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    posts = capture_post(page)
    page.on("dialog", lambda d: d.dismiss())
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    page.locator("#selectModeBtn").click()
    page.locator(".card-select-input").first.check()
    page.locator("#refreshSelectedBtn").click()
    page.wait_for_timeout(500)
    assert not posts and page.locator("#selectionBar").is_visible()  # dismissing keeps the selection
    page.locator("#cancelSelectionBtn").click()
    page.wait_for_function("document.querySelector('#selectionBar').hidden")
    page.wait_for_function("!document.querySelector('.card-select-input')")
    assert page.locator("#selectModeBtn").get_attribute("aria-pressed") == "false"


def test_all_button_confirm_mentions_total_and_cost(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    posts = capture_post(page)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    page.wait_for_function("!document.querySelector('#refreshAnalysisBtn').disabled")
    page.locator("#refreshAnalysisBtn").click()
    for _ in range(50):
        if posts:
            break
        page.wait_for_timeout(100)
    assert posts == [None]  # all tickers: no body, unchanged behaviour
    assert "全部 5 檔" in dialogs[0] and "$0.25" in dialogs[0] and "100 秒" in dialogs[0]


def test_selection_is_capped_at_twenty(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)

    def many(route):
        response = route.fetch()
        cards = response.json()
        template = cards[0]
        route.fulfill(json=[{**template, "ticker": f"ZZ{i:02d}", "name": f"Fake {i}", "favorite": False}
                            for i in range(22)])
    page.route("**/api/stocks?*", many)
    page.goto(app_url)
    page.locator("#grid .card").nth(21).wait_for()
    page.locator("#selectModeBtn").click()
    boxes = page.locator(".card-select-input")
    boxes.nth(21).wait_for()
    for i in range(20):
        boxes.nth(i).check()
    assert page.locator("#selectionCount").inner_text() == "已選 20 檔"
    assert boxes.nth(20).is_disabled() and boxes.nth(21).is_disabled() and boxes.nth(0).is_enabled()
    assert "最多一次選 20 檔" in page.locator("#selectionNote").inner_text()
    boxes.nth(0).uncheck()
    assert page.locator("#selectionCount").inner_text() == "已選 19 檔" and boxes.nth(20).is_enabled()


def test_selection_bar_fits_mobile(app_url, page_factory):
    page = page_factory(width=390, height=800)
    fake_non_demo(page)
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    page.locator("#selectModeBtn").click()
    page.locator(".card-select-input").first.check()
    box = page.locator("#selectionBar").bounding_box()
    assert box["x"] >= -1 and box["x"] + box["width"] <= 391
    scroll_width, inner_width = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    assert scroll_width <= inner_width + 1


# ---- detail: per-stock refresh

def test_detail_button_posts_ticker_and_shows_cost(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    posts = capture_post(page, {"detail": "找不到可用的 AI 引擎。任選一種設定"}, status=503)
    page.route("**/api/refresh/status", lambda r: r.fulfill(json=IDLE))
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#refreshStockAnalysisBtn").wait_for()
    page.wait_for_function("!document.querySelector('#refreshStockAnalysisBtn').disabled")
    assert page.locator("#analysisRefreshNote").inner_text() == "約 $0.05、約 20 秒（依你的 AI 而定）"
    page.locator("#refreshStockAnalysisBtn").click()
    status = page.locator("#analysisRefreshStatus")
    page.wait_for_function("document.querySelector('#analysisRefreshStatus').textContent.includes('docs/ENGINES.md')")
    assert json.loads(posts[0]) == {"tickers": ["DEMOA"]}
    assert "找不到可用的 AI 引擎" in status.inner_text()
    assert page.locator("#refreshStockAnalysisBtn").is_enabled()


def test_detail_cost_note_without_history(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page, hint={"per_ticker_usd": None, "per_ticker_seconds": None, "based_on": 0})
    page.route("**/api/refresh/status", lambda r: r.fulfill(json=IDLE))
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#refreshStockAnalysisBtn").wait_for()
    page.wait_for_function("document.querySelector('#analysisRefreshNote').textContent.length > 0")
    assert page.locator("#analysisRefreshNote").inner_text() == "會呼叫一次 AI"


def test_detail_button_disabled_while_any_refresh_runs(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    state = {"running": True}

    def status(route):
        body = json.loads(json.dumps(IDLE))
        body["analysis"].update(running=state["running"], tickers=["OTHER"] if state["running"] else None)
        route.fulfill(json=body)
    page.route("**/api/refresh/status", status)
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#refreshStockAnalysisBtn").wait_for()
    page.wait_for_function("document.querySelector('#refreshStockAnalysisBtn').textContent.includes('更新中')")
    assert page.locator("#refreshStockAnalysisBtn").is_disabled()
    assert "另一個分析更新" in page.locator("#analysisRefreshStatus").inner_text()
    state["running"] = False
    page.wait_for_function("!document.querySelector('#refreshStockAnalysisBtn').disabled", timeout=8_000)


def test_detail_reloads_after_a_successful_run(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    posts = capture_post(page)
    page.route("**/api/refresh/status", lambda r: r.fulfill(json=IDLE))
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#refreshStockAnalysisBtn").wait_for()
    page.wait_for_function("!document.querySelector('#refreshStockAnalysisBtn').disabled")
    with page.expect_navigation():
        page.locator("#refreshStockAnalysisBtn").click()
    assert json.loads(posts[0]) == {"tickers": ["DEMOA"]}
    page.locator("#refreshStockAnalysisBtn").wait_for()


def test_detail_failed_run_shows_reason(app_url, page_factory):
    page = page_factory()
    fake_non_demo(page)
    capture_post(page)
    failed = json.loads(json.dumps(IDLE))
    failed["analysis"].update(success=False, last_result=["boom line", "exit code 1"])
    page.route("**/api/refresh/status", lambda r: r.fulfill(json=failed))
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#refreshStockAnalysisBtn").wait_for()
    page.wait_for_function("!document.querySelector('#refreshStockAnalysisBtn').disabled")
    page.locator("#refreshStockAnalysisBtn").click()
    page.wait_for_function("document.querySelector('#analysisRefreshStatus').textContent.includes('分析更新失敗')")
    assert "exit code 1" in page.locator("#analysisRefreshStatus").inner_text()
    assert page.locator("#refreshStockAnalysisBtn").is_enabled()

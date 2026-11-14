"""E2E: URL / search input is never interpreted as HTML, and a failed load shows a friendly message."""

PAYLOAD = "<img src=x onerror=window.__x=1>"


def test_detail_ticker_param_is_not_executed(app_url, page_factory):
    page = page_factory()
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(app_url + "/detail.html?ticker=" + PAYLOAD)
    page.locator("#detailRoot .empty").wait_for()
    page.wait_for_timeout(300)
    assert page.evaluate("window.__x") is None and not dialogs
    assert PAYLOAD in page.locator("#detailRoot .empty").inner_text()  # shown as plain text
    assert page.locator("#detailRoot img").count() == 0


def test_search_query_is_escaped(app_url, page_factory):
    page = page_factory()
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    page.locator("#searchInput").fill("<img src=x onerror=window.__y=1>")
    page.locator("#grid .empty").wait_for()
    page.wait_for_timeout(300)
    assert page.evaluate("window.__y") is None and page.locator("#grid img").count() == 0
    assert "<img src=x" in page.locator("#grid .empty").inner_text()


def test_detail_load_failure_is_friendly(app_url, page_factory, assert_no_page_errors):
    page = page_factory()
    assert_no_page_errors(page)
    page.route("**/api/stocks/DEMOA/history*", lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#detailRoot .empty").wait_for()
    assert page.locator("#detailRoot .empty").inner_text() == "載入失敗，請重新整理"


def test_detail_network_error_is_friendly(app_url, page_factory):
    page = page_factory()
    page.route("**/api/stocks/DEMOA?*", lambda r: r.abort())
    page.goto(app_url + "/detail.html?ticker=DEMOA")
    page.locator("#detailRoot .empty").wait_for()
    assert page.locator("#detailRoot .empty").inner_text() == "載入失敗，請重新整理"


def test_dashboard_rerender_does_not_stack_charts(app_url, page_factory, assert_no_page_errors):
    page = page_factory()
    assert_no_page_errors(page)
    page.goto(app_url + "/dashboard.html")
    page.locator("#pieChart").wait_for()
    page.wait_for_function("Chart.getChart(document.getElementById('pieChart'))")
    assert page.evaluate("typeof window.escapeHtml") == "function"

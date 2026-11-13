import json


def _present_as_normal_mode(page):
    page.route(
        "**/api/info",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"demo": False, "version": "e2e", "engine": {}}),
        ),
    )


def test_overview_selection_and_confirmation(app_url, page_factory):
    page = page_factory()
    _present_as_normal_mode(page)
    page.goto(app_url + "/overview.html")
    page.locator("#tickerPicker input[type=checkbox]").first.wait_for()
    boxes = page.locator("#tickerPicker input[type=checkbox]")
    assert boxes.count() == 5

    for index in range(boxes.count()):
        boxes.nth(index).check()
    assert page.locator("#tickerPicker input[type=checkbox]:checked").count() <= 10

    messages = []
    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
    page.locator("#runBatchBtn").click()
    page.wait_for_timeout(300)
    assert messages and "5" in messages[0]
    assert page.evaluate("fetch('/api/strategy/batch').then(r => r.json()).then(d => d.status)") != "running"

    for index in range(boxes.count()):
        boxes.nth(index).uncheck()
    page.get_by_role("button", name="勾選台股前3檔").click()
    page.get_by_role("button", name="勾選美股前3檔").click()
    assert page.locator("#tickerPicker input[type=checkbox]:checked").count() == 5


def test_overview_escapes_hostile_batch_and_shows_409(app_url, page_factory):
    page = page_factory()
    _present_as_normal_mode(page)
    hostile = {
        "status": "done",
        "progress": {"done": 1, "total": 1, "current": None},
        "overview": {
            "rules": [{
                "key": "k",
                "rule": "<img src=x onerror=window.__batchXss=1>",
                "file": "<b>file</b>",
                "heading": "heading",
                "count_fail": 1,
                "count_pass": 0,
                "count_unknown": 0,
                "count_conditional": 0,
            }],
            "tickers": [{
                "ticker": "<svg onload=window.__batchXss=2>",
                "rows": {"k": "fail"},
                "counts": {"pass": 0, "fail": 1, "unknown": 0, "conditional": 0},
                "holdings_lots": 0,
                "generated_at": "e2e",
            }],
        },
        "errors": {},
    }

    def batch_route(route):
        if route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(hostile))
        else:
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": "已經有一個策略檢查在執行中，稍等一下"}),
            )

    page.route("**/api/strategy/batch", batch_route)
    page.goto(app_url + "/overview.html")
    page.locator("#batchMatrix").wait_for()
    assert page.evaluate("window.__batchXss") is None
    assert page.locator("img[src='x'], svg[onload]").count() == 0
    assert "<img src=x" in page.locator("#batchMatrix").inner_text()

    page.get_by_role("button", name="勾選台股前3檔").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#runBatchBtn").click()
    page.get_by_text("已經有一個策略檢查在執行中", exact=False).wait_for()

def test_empty_watchlist_shows_first_run_card_and_focuses_ticker_input(empty_app_url, page_factory):
    page = page_factory()
    page.goto(empty_app_url)
    card = page.locator("#grid .first-run-empty")
    card.wait_for()
    text = card.inner_text()
    assert "還沒有任何股票" in text
    assert "TSLA" in text and "2330" in text
    assert "claude / codex CLI" in text
    assert "設定說明" in text
    assert "docs/ENGINES.md" in (card.locator("a").first.get_attribute("href") or "")
    assert "./scripts/run_demo.sh" in text
    assert "docker compose up" in text
    assert "watchlist 是空的" not in text

    card.get_by_role("button", name="把游標移到上方輸入框").click()
    assert page.evaluate("document.activeElement.id") == "newTicker"


def test_empty_watchlist_tour_has_first_run_step(empty_app_url, page_factory):
    page = page_factory(consent=True, tours=False)
    page.goto(empty_app_url)
    popover = page.locator(".tour-popover")
    popover.wait_for()
    assert "從第一檔股票開始" in popover.inner_text()


def test_nonempty_filtered_result_keeps_normal_empty_message(app_url, page_factory):
    page = page_factory()
    page.goto(app_url)
    page.locator("#grid .card").first.wait_for()
    page.locator("#searchInput").fill("NOT-A-DEMO-TICKER")
    empty = page.locator("#grid .empty")
    empty.wait_for()
    assert "找不到符合" in empty.inner_text()
    assert page.locator("#grid .first-run-empty").count() == 0

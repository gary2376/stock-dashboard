import pytest


PAGES = (
    ("/", "index"),
    ("/dashboard.html", "dashboard"),
    ("/detail.html?ticker=DEMOA", "detail"),
    ("/strategy.html", "strategy"),
    ("/overview.html", "overview"),
)


@pytest.mark.parametrize("width", [1100, 390])
def test_tour_runs_on_every_page_and_stays_in_view(app_url, page_factory, width):
    page = page_factory(width=width, height=800, consent=True, tours=False)

    for path, page_name in PAGES:
        page.goto(app_url + path)
        popover = page.locator(".tour-popover[role='dialog']")
        popover.wait_for(timeout=10_000)
        steps = 0
        while popover.count() and steps < 20:
            box = popover.bounding_box()
            assert box is not None
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1
            assert box["y"] >= -1 and box["y"] + box["height"] <= 801
            scroll_width, inner_width = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
            assert scroll_width <= inner_width + 1
            steps += 1
            done = popover.get_by_role("button", name="完成")
            if done.count():
                done.click()
                break
            popover.get_by_role("button", name="下一步").click()
        assert steps >= 2, page_name
        assert popover.count() == 0

        page.reload()
        page.wait_for_timeout(500)
        assert page.locator(".tour-popover[role='dialog']").count() == 0
        page.locator("button:has-text('導覽')").first.click()
        popover.wait_for()
        page.keyboard.press("Escape")
        assert popover.count() == 0

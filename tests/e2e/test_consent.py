def test_consent_uses_modal_dialog_and_traps_focus(app_url, page_factory):
    page = page_factory(consent=False, tours=False)
    page.goto(app_url)
    dialog = page.locator("dialog.consent-dialog[open]")
    dialog.wait_for()

    assert page.evaluate("document.querySelector('dialog.consent-dialog').matches(':modal')")
    assert page.evaluate("document.activeElement.classList.contains('consent-confirm-btn')")
    page.keyboard.press("Escape")
    assert dialog.is_visible()
    page.mouse.click(5, 5)
    assert dialog.is_visible()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("!!document.activeElement.closest('dialog.consent-dialog')")
    assert page.locator(".tour-popover").count() == 0

    page.locator(".consent-confirm-btn").click()
    assert dialog.count() == 0
    assert page.evaluate("localStorage.getItem('sd_consent_v1')") == "1"
    page.reload()
    assert page.locator("dialog.consent-dialog[open]").count() == 0


def test_consent_dialog_fits_mobile_viewport(app_url, page_factory):
    page = page_factory(width=390, height=700, consent=False, tours=False)
    page.goto(app_url)
    dialog = page.locator("dialog.consent-dialog[open]")
    dialog.wait_for()
    box = dialog.bounding_box()

    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] >= 0 and box["y"] + box["height"] <= 700
    scroll_width, inner_width = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    assert scroll_width <= inner_width + 1

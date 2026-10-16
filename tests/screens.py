"""Every screen of NUauto's page, opened one after another: the same check for the laptop's app (test_gui.py) and the
homelab's page (test_homelab_app.py), so a change to the page (gui_static/) is tried on both. Not a test by itself."""

# every screen in app.js (screens.<name>), and the old #/company, #/other, #/sheet (they land on Apply); #/assistant/<slot>
# needs a session
ROUTES = ("home", "review", "apply", "company", "other", "sheet", "insights", "answers", "settings", "logs", "setup")
NAV = ["home", "review", "apply", "insights", "answers", "settings"]  # the menu: the same on both


def visit_all(page, origin):
    """Opens each screen; -> (the menu's entries, {route: what went wrong}) for screens that showed an error toast or
    nothing at all. Page errors are the caller's (its pageerror listener)."""
    bad = {}
    for route in ROUTES:
        page.goto(f"{origin}/#/{route}")
        # (polled from here: the page's CSP blocks Playwright's wait_for_function, which evals its predicate)
        for _ in range(100):
            if page.evaluate("() => { const v = document.querySelector('#view'); return !!v && v.children.length > 0 && "
                             "!v.querySelector(':scope > .spinner, :scope > p > .spinner'); }"):
                break
            page.wait_for_timeout(200)
        else:
            bad[route] = "nothing shown"
            continue
        page.wait_for_timeout(300)
        errors = page.locator(".toast.error").all_inner_texts()
        if errors:
            bad[route] = errors
        page.evaluate("document.querySelectorAll('.toast').forEach((t) => t.remove())")
    nav = page.eval_on_selector_all(".side nav a", "(as) => as.filter((a) => !a.hidden && a.offsetParent !== null)"
                                    ".map((a) => a.dataset.route)")
    return nav, bad

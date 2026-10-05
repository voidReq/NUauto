"""Read-only look at an application form. Fills nothing, submits nothing.

  nuauto inspect <job-url>

Clicks the job page's single "Apply" button, then lists the form fields
(label, type, required, options) and saves a screenshot under logs/.
"""
import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

from nuauto import browser
from nuauto import config

# Collects visible form controls. Reports whether a field already has a value, never the value.
FIELDS_JS = """
(root) => {
  root = root || document;
  root.querySelectorAll('[data-fill-idx]').forEach(e => e.removeAttribute('data-fill-idx'));
  const label = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText.trim(); }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText.trim();
    const aria = el.getAttribute('aria-label'); if (aria) return aria.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ').trim(); if (t) return t; }
    const grp = el.closest('fieldset'); if (grp && grp.querySelector('legend')) return grp.querySelector('legend').innerText.trim();
    return '';
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  return [...root.querySelectorAll('input, select, textarea')]
    .filter(el => el.type !== 'hidden' && visible(el))
    .map((el, i) => { el.setAttribute('data-fill-idx', i); return {
      idx: i,
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      name: el.name || '',
      label: label(el),
      required: el.required || el.getAttribute('aria-required') === 'true',
      has_value: el.type === 'checkbox' || el.type === 'radio' ? el.checked : !!el.value,
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) :
               (el.type === 'radio' ? [el.closest('label') ? el.closest('label').innerText.trim() : el.value] : []),
    }; });
}
"""


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    url = sys.argv[1]
    if not browser.host_allowed(url):
        sys.exit(f"Refusing: {url!r} is not on an allowed host.")
    log = browser.RunLog("inspect_form")
    blocked = []
    with sync_playwright() as p:
        context = browser.launch(p)
        try:
            browser.install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            log.write(f"Opening {url}")
            if not browser.goto_logged_in(page, context, url, log):
                log.write("STOP: not logged in. Run: nuauto login")
                return

            # the page's text is lowercase "apply" (capitalized by CSS), so ignore case
            apply_name = re.compile(r"^\s*apply\s*$", re.IGNORECASE)
            apply_btn = page.get_by_role("button", name=apply_name)
            if apply_btn.count() != 1:
                apply_btn = page.get_by_role("link", name=apply_name)
            if apply_btn.count() != 1:
                log.write(f"STOP: expected exactly one Apply button, found {apply_btn.count()}. Needs Human.")
                log.screenshot(page, "no_apply_button")
                return

            log.write("Clicking Apply (opens the form; nothing is filled or submitted).")
            apply_btn.click()
            browser.pause(page, 3, 5)

            if blocked:
                log.write(f"STOP: Apply leads off the allowed domain: {blocked}. Needs Human.")
                return

            for i, pg in enumerate(context.pages):
                log.write(f"Open page {i}: {pg.url}")
            page = context.pages[-1]
            log.screenshot(page, "application_form")

            fields = page.evaluate(FIELDS_JS)
            path = os.path.join(log.dir, "fields.json")
            with open(path, "w") as f:
                json.dump(fields, f, indent=2)
            log.write(f"{len(fields)} visible fields saved to {path}")
            for fld in fields:
                req = "*" if fld["required"] else " "
                log.write(f" {req} [{fld['tag']}/{fld['type']}] {fld['label']!r} options={fld['options']} prefilled={fld['has_value']}")
        except KeyboardInterrupt:
            log.write("Ctrl+C: closing browser.")
        finally:
            context.close()


if __name__ == "__main__":
    main()

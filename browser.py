"""Playwright (Firefox) with a persistent profile.

  python browser.py login          # you log in by hand (SSO); session is saved
  python browser.py open <job-url> # open one job page, screenshot, stop
"""
import contextlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

import config


def pause(page, low=1.5, high=4.0):
    # wait_for_timeout (not time.sleep) so Playwright keeps handling events
    page.wait_for_timeout(random.uniform(low, high) * 1000)


_extra_hosts = set()  # only filled inside sso_hosts_allowed()


def host_allowed(url):
    if "\\" in url:  # browsers read "\" as "/", so urlparse and Firefox could disagree on the host
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname in config.ALLOWED_HOSTS or parsed.hostname in _extra_hosts)


@contextlib.contextmanager
def sso_hosts_allowed():
    _extra_hosts.update(config.SSO_HOSTS)
    try:
        yield
    finally:
        _extra_hosts.clear()


class RunLog:
    """One folder per run under logs/, with actions.log and screenshots."""

    def __init__(self, label):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(config.LOGS_DIR, f"{stamp}_{label}")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "actions.log")

    def write(self, msg):
        line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
        print(line)
        with open(self.path, "a") as f:
            f.write(line + "\n")

    def screenshot(self, page, name):
        path = os.path.join(self.dir, f"{name}.png")
        page.screenshot(path=path, full_page=True)
        self.write(f"screenshot saved: {path}")


def save_cookies(cookies):
    """Firefox drops session cookies on close, so keep them in a mode-600 file."""
    fd = os.open(config.COOKIES_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cookies, f)
    os.chmod(config.COOKIES_PATH, 0o600)


def launch(p):
    os.makedirs(config.PROFILE_DIR, mode=0o700, exist_ok=True)
    os.chmod(config.PROFILE_DIR, 0o700)
    headless = os.environ.get("AUTO_HEADLESS") == "1"  # set by daily.py only
    context = p.firefox.launch_persistent_context(config.PROFILE_DIR, headless=headless)
    if os.path.exists(config.COOKIES_PATH):
        with open(config.COOKIES_PATH) as f:
            context.add_cookies(json.load(f))
    return context


def relogin(page, context, log):
    """One click on 'Current Students And Alumni'. If the SSO session is still alive it
    passes straight through. Never types credentials. Returns True once back in NUworks."""
    name = re.compile("current students", re.IGNORECASE)
    btn = page.get_by_role("button", name=name)
    if btn.count() != 1:
        btn = page.get_by_role("link", name=name)
    if btn.count() != 1:
        log.write("Auto re-login: sign-in button not found.")
        return False
    log.write("Session expired: trying one-click re-login (SSO hosts allowed for this step only).")
    with sso_hosts_allowed():
        btn.click(timeout=10000)
        for _ in range(25):
            page.wait_for_timeout(1000)
            try:
                if page.locator("input[type=password]").count():
                    log.write("Auto re-login: SSO is asking for a password. Run `python browser.py login`.")
                    return False
                if urlparse(page.url).hostname in config.ALLOWED_HOSTS and not on_login_page(page):
                    save_cookies(context.cookies())
                    log.write("Auto re-login succeeded; session cookies refreshed.")
                    return True
            except Exception:
                continue  # page was mid-navigation
    log.write("Auto re-login: did not get back to NUworks in time.")
    return False


def on_login_page(page):
    """NUworks' expired-session screens: "Students: Sign-in method", or a "Log in" page that embeds the SSO form."""
    t = page.title().lower()
    return "sign-in" in t or "log in" in t or "login" in t


def goto(page, url, log, retry_wait=60):
    """page.goto, but a page-load timeout (NUworks is sometimes slow) gets one more try after retry_wait seconds."""
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PlaywrightTimeout:
        log.write(f"Page load timed out; trying once more in {retry_wait}s.")
        page.wait_for_timeout(retry_wait * 1000)
        page.goto(url, wait_until="domcontentloaded")


def goto_logged_in(page, context, url, log):
    """Open a NUworks page, doing the one-click re-login if the session expired.
    Returns False if still not logged in."""
    goto(page, url, log)
    pause(page)
    if not on_login_page(page):
        return True
    if "sign-in" not in page.title().lower():  # the "Log in" variant: load the sign-in screen with the button
        goto(page, config.NUWORKS_START_URL.split("/app/")[0] + "/", log)
        pause(page)
    if not relogin(page, context, log):
        return False
    goto(page, url, log)
    pause(page)
    return not on_login_page(page)


def install_domain_lock(context, log, blocked):
    """Abort any page/frame navigation to a host outside ALLOWED_HOSTS."""
    def handler(route):
        req = route.request
        if req.is_navigation_request() and not host_allowed(req.url):
            route.abort()
            try:
                main_frame = req.frame.parent_frame is None
            except Exception:
                main_frame = True  # can't tell: treat as the page itself leaving
            if main_frame:
                blocked.append(req.url)
                log.write(f"BLOCKED navigation to {urlparse(req.url).hostname}")
            else:
                # e.g. an embedded YouTube video in a job description: blocked, but the page stays on NUworks
                log.write(f"blocked embedded frame from {urlparse(req.url).hostname}")
        else:
            route.continue_()
    context.route("**/*", handler)


def cmd_login():
    log = RunLog("login")
    with sync_playwright() as p:
        context = launch(p)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.NUWORKS_START_URL)
            log.write("Browser open. Log in by hand (no domain lock in this mode).")
            log.write("When you can see the jobs page, close the Firefox window to save the session.")
            last = None
            while True:
                try:
                    page.wait_for_timeout(2000)  # keeps Playwright's events flowing
                    cookies = context.cookies()
                except Exception:
                    break  # window closed
                if cookies and cookies != last:
                    save_cookies(cookies)  # snapshot while the browser is still open
                    last = cookies
            log.write("Browser closed. Session cookies saved to session_cookies.json (mode 600).")
        except KeyboardInterrupt:
            log.write("Ctrl+C: closing browser.")
        finally:
            try:
                context.close()
            except Exception:
                pass


def cmd_open(url):
    if not host_allowed(url):
        sys.exit(f"Refusing: {url!r} is not an https URL on an allowed host {sorted(config.ALLOWED_HOSTS)}.")
    log = RunLog("open")
    blocked = []
    with sync_playwright() as p:
        context = launch(p)
        try:
            install_domain_lock(context, log, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            log.write(f"Opening {url}")
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as e:
                if not blocked:
                    raise
                log.write(f"Navigation stopped: {type(e).__name__}")
            pause(page)
            if blocked:
                log.write("STOP: redirected off the allowed domain (session expired? run `login`). Needs Human.")
                return
            log.write(f"Page title: {page.title()!r}")
            log.screenshot(page, "job_page")
        except KeyboardInterrupt:
            log.write("Ctrl+C: closing browser.")
        finally:
            context.close()


def main():
    args = sys.argv[1:]
    if args == ["login"]:
        cmd_login()
    elif len(args) == 2 and args[0] == "open":
        cmd_open(args[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

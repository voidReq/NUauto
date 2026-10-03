"""Offline checks for the domain lock in browser.py (no browser is launched). Run: python test_browser.py"""
import browser
import config

NUW = "https://northeastern-csm.symplicity.com"


class FakeFrame:
    def __init__(self, main=True):
        self.parent_frame = None if main else object()


class FakeRequest:
    def __init__(self, url, navigation=True, frame=None):
        self.url, self.navigation, self.frame = url, navigation, frame or FakeFrame()

    def is_navigation_request(self):
        return self.navigation


class FakeRoute:
    def __init__(self, request):
        self.request, self.did = request, []

    def abort(self):
        self.did.append("abort")

    def continue_(self):
        self.did.append("continue")


class FakeContext:
    def route(self, pattern, handler):
        self.pattern, self.handler = pattern, handler


class Log:
    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(msg)


def lock():
    ctx, log, blocked = FakeContext(), Log(), []
    browser.install_domain_lock(ctx, log, blocked)
    return ctx, log, blocked


def navigate(ctx, url, **kw):
    route = FakeRoute(FakeRequest(url, **kw))
    ctx.handler(route)
    return route.did


def main():
    # config sanity: exactly one allowed host, SSO hosts are separate and not allowed by default
    assert config.ALLOWED_HOSTS == {"northeastern-csm.symplicity.com"}
    assert config.SSO_HOSTS and not (config.SSO_HOSTS & config.ALLOWED_HOSTS)
    assert browser._extra_hosts == set()

    # https NUworks host is allowed (any path, query, port, host case)
    for u in [NUW, NUW + "/", NUW + "/students/app/jobs/detail/123?x=1#y", NUW + ":8443/x", NUW.upper().replace("HTTPS", "https")]:
        assert browser.host_allowed(u), u

    # plain http is rejected, as are other schemes
    for u in ["http://northeastern-csm.symplicity.com/", "ftp://northeastern-csm.symplicity.com/",
              "file:///etc/passwd", "about:blank", "javascript:alert(1)", "data:text/html,hi", "//northeastern-csm.symplicity.com/", ""]:
        assert not browser.host_allowed(u), u

    # other hosts and look-alikes are rejected
    for u in ["https://evil.example/", "https://google.com/", "https://symplicity.com/",
              "https://northeastern-csm.symplicity.com.evil.example/",
              "https://evil.example/?x=northeastern-csm.symplicity.com",
              "https://evil.example/northeastern-csm.symplicity.com",
              "https://evil.example#@northeastern-csm.symplicity.com/",
              "https://evil.example?@northeastern-csm.symplicity.com/",
              "https://xnortheastern-csm.symplicity.com/", "https://northeastern-csm.symplicity.com./",
              "https://evil-northeastern-csm.symplicity.com/", "https://sub.northeastern-csm.symplicity.com/",
              "https://northeastern-csm.symplicity.com@evil.example/",
              "https://northeastern-csm.symplicity.com:pw@evil.example/",
              "https://northeastern-csm.symplicity.com%2f@evil.example/"]:
        assert not browser.host_allowed(u), u

    # SSO hosts: rejected normally, allowed (https only) inside the re-login window, cleared afterwards
    sso = [f"https://{h}/idp/x" for h in sorted(config.SSO_HOSTS)]
    assert not any(browser.host_allowed(u) for u in sso)
    with browser.sso_hosts_allowed():
        assert all(browser.host_allowed(u) for u in sso)
        assert browser.host_allowed(NUW + "/")
        assert not any(browser.host_allowed(u.replace("https", "http")) for u in sso)
        assert not browser.host_allowed("https://evil.example/")
        assert not browser.host_allowed("https://" + sorted(config.SSO_HOSTS)[0] + ".evil.example/")
    assert browser._extra_hosts == set() and not any(browser.host_allowed(u) for u in sso)

    # ...also cleared when the code inside the window raises (e.g. a click timeout)
    try:
        with browser.sso_hosts_allowed():
            assert all(browser.host_allowed(u) for u in sso)
            raise TimeoutError("click timed out")
    except TimeoutError:
        pass
    assert browser._extra_hosts == set() and not any(browser.host_allowed(u) for u in sso)

    # ...and when the window is left by Ctrl+C
    try:
        with browser.sso_hosts_allowed():
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    assert browser._extra_hosts == set()

    # route handler: allowed navigation continues
    ctx, log, blocked = lock()
    assert ctx.pattern == "**/*"
    assert navigate(ctx, NUW + "/students/app/jobs/discover") == ["continue"]
    assert blocked == []

    # off-domain page navigation is aborted, recorded as blocked, and logged by host
    assert navigate(ctx, "https://evil.example/login") == ["abort"]
    assert navigate(ctx, "http://northeastern-csm.symplicity.com/") == ["abort"]
    assert blocked == ["https://evil.example/login", "http://northeastern-csm.symplicity.com/"]
    assert any("BLOCKED navigation to evil.example" in x for x in log.lines)

    # SSO host navigation: blocked normally, continues inside the window, blocked again after it
    sso_url = sso[0]
    assert navigate(ctx, sso_url) == ["abort"]
    with browser.sso_hosts_allowed():
        assert navigate(ctx, sso_url) == ["continue"]
    assert navigate(ctx, sso_url) == ["abort"]

    # off-domain navigation of a sub-frame: aborted but NOT recorded as the page leaving
    ctx, log, blocked = lock()
    assert navigate(ctx, "https://www.youtube.com/embed/x", frame=FakeFrame(main=False)) == ["abort"]
    assert blocked == [] and any("blocked embedded frame from www.youtube.com" in x for x in log.lines)

    # a frame that can't be inspected counts as the page itself leaving
    class BrokenFrame:
        @property
        def parent_frame(self):
            raise RuntimeError("detached")
    assert navigate(ctx, "https://evil.example/", frame=BrokenFrame()) == ["abort"]
    assert blocked == ["https://evil.example/"]

    # non-navigation requests (images, XHR, scripts) are not the lock's job: they pass through
    assert navigate(ctx, "https://cdn.example/a.png", navigation=False) == ["continue"]
    assert blocked == ["https://evil.example/"]

    # `browser.py open <url>` refuses a disallowed URL before launching anything
    for u in ["https://evil.example/", "http://northeastern-csm.symplicity.com/x", "https://northeastern-csm.symplicity.com@evil.example/"]:
        try:
            browser.cmd_open(u)
        except SystemExit as e:
            assert "Refusing" in str(e.code), e.code
        else:
            raise AssertionError(f"cmd_open accepted {u!r}")

    # Backslash trick: browsers read "\" as "/" in https URLs, so this goes to evil.example.
    # (last on purpose: urlparse sees host northeastern-csm.symplicity.com, so host_allowed says True, see report)
    assert not browser.host_allowed("https://evil.example\\@northeastern-csm.symplicity.com/")

    print("All browser domain-lock checks passed.")


if __name__ == "__main__":
    main()

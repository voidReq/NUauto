#!/usr/bin/env python3
"""NUauto's own window on Linux: a GTK window with a web view on NUauto's local page.

Run by the system's python3, not by NUauto's own Python: the system one has GTK and WebKitGTK (python3-gobject +
webkitgtk, on most GNOME desktops); bundling GTK into the app would break across distros. So this file imports
nothing from nuauto. Links to other sites open in your normal browser; the web view only ever shows NUauto.

  python3 window_gtk.py --check                  exit 0 if this system can show the window
  python3 window_gtk.py <url> [icon.png|svg]      show it; exits when the window is closed
"""
import sys
from urllib.parse import urlparse

# (Gtk version, WebKit namespace, its version), newest first. WebKit is checked before Gtk is required: once a Gtk
# version is required, another can't be.
CHOICES = [("4.0", "WebKit", "6.0"), ("3.0", "WebKit2", "4.1"), ("3.0", "WebKit2", "4.0")]


def load():
    import gi
    repo = gi.Repository.get_default()
    for gtk, name, version in CHOICES:
        try:
            if version in repo.enumerate_versions(name) and gtk in repo.enumerate_versions("Gtk"):
                gi.require_version("Gtk", gtk)
                gi.require_version(name, version)
                from gi.repository import Gio, Gtk
                webkit = getattr(__import__("gi.repository", fromlist=[name]), name)
                return gtk, Gtk, webkit, Gio
        except (ValueError, ImportError):
            continue
    raise ImportError("no GTK + WebKitGTK")


def hook(view, webkit, gio, url):
    """Only NUauto's own page stays in the window; any other link (and window.open) goes to your browser."""
    origin = urlparse(url).netloc

    def outside(uri):
        u = urlparse(uri or "")
        return u.scheme in ("http", "https") and u.netloc != origin

    def browser(uri):
        try:
            gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            pass

    def decide(view, decision, kind):
        if kind in (webkit.PolicyDecisionType.NAVIGATION_ACTION, webkit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if outside(uri) or kind == webkit.PolicyDecisionType.NEW_WINDOW_ACTION:
                browser(uri)
                decision.ignore()
                return True
        return False

    def create(view, action):
        browser(action.get_request().get_uri())
        return None
    view.connect("decide-policy", decide)
    view.connect("create", create)
    settings = view.get_settings()
    settings.set_enable_developer_extras(False)


def show(url, icon=None):
    gtk_version, Gtk, webkit, gio = load()
    view = webkit.WebView()
    hook(view, webkit, gio, url)
    view.load_uri(url)
    if gtk_version == "4.0":
        app = Gtk.Application()

        def activate(app):
            win = Gtk.ApplicationWindow(application=app, title="NUauto")
            win.set_default_size(1200, 860)
            win.set_icon_name("nuauto")  # from the icon theme, if the app icon was added
            win.set_child(view)
            win.present()
        app.connect("activate", activate)
        return app.run([])
    win = Gtk.Window(title="NUauto")
    win.set_default_size(1200, 860)
    if icon:
        try:
            win.set_icon_from_file(icon)
        except Exception:
            pass
    win.add(view)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        try:
            load()
        except Exception:
            sys.exit(1)
        sys.exit(0)
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(show(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))

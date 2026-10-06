"""Smoke test for a built NUauto package. CI runs it after packaging/build.sh; you can too:

  packaging/build/venv/bin/python packaging/smoke.py dist/NUauto-<version>-linux-x86_64.AppImage
  packaging/build/venv/bin/python packaging/smoke.py packaging/build/dist/NUauto.app/Contents/MacOS/NUauto

1. the app's own commands answer (`doctor --json`, `_playwright --version`);
2. which window this system gets (`_window`; macOS must get its own);
3. `selftest`: demo mode end to end in headless Firefox (approve, apply with a question, the fake sheet), run by
   the app itself (src/nuauto/selftest.py).
Uses a temp HOME, so a real NUauto setup on this machine is never touched. Exits non-zero on any failure.
"""
import json
import os
import subprocess
import sys
import tempfile

APP = os.path.abspath(sys.argv[1])
HOME = tempfile.mkdtemp(prefix="nuauto-smoke-home-")
REAL_CACHE = os.path.expanduser("~/Library/Caches/ms-playwright" if sys.platform == "darwin" else "~/.cache/ms-playwright")
ENV = {**os.environ, "HOME": HOME, "APPIMAGE_EXTRACT_AND_RUN": "1", "NUAUTO_DEMO_PACE": "0.02",
       "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", REAL_CACHE)}
for k in ("NUAUTO_STATE_DIR", "NUAUTO_DEMO", "CLAUDECODE"):
    ENV.pop(k, None)


def run(*args, timeout=600):
    r = subprocess.run([APP, *args], env=ENV, capture_output=True, text=True, timeout=timeout, cwd=HOME)
    return r.returncode, r.stdout, r.stderr


code, out, err = run("doctor", "--json")
report = json.loads(out)  # nothing set up in this HOME: problems expected, but a real answer
assert "checks" in report and any(c["msg"].startswith("Settings:") for c in report["checks"]), out
print("doctor --json: ok")
code, out, err = run("_playwright", "--version")
assert code == 0 and "Version" in out, (code, out, err)
print("bundled Playwright:", out.strip())
code, out, err = run("_window")
print("window on this system:", out.strip())
if sys.platform == "darwin":
    assert out.strip() == "mac", (out, err)  # the app's own WebKit window (pywebview is in the bundle)
code, out, err = run("selftest", timeout=1500)  # Firefox through the app, then demo mode end to end
print(out.strip())
assert code == 0 and "Self-test passed." in out, (out[-1500:], err[-1500:])

print("Smoke test passed.")

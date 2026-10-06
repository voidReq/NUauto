"""The setup wizard (onboard.py + the GUI's Setup screen) on a fresh demo. Run: python test_setup.py

First the step checks one by one (client file, sheet address, sheet setup that never overwrites, resume PDF,
preferences, term list, Discord, the scheduler), then the whole wizard in headless Firefox from an empty start
to Today. Demo mode: fake Google, fake NUworks pages, fake Claude; no network.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile

STATE = tempfile.mkdtemp(prefix="nuauto-test-setup-")
os.environ.update(NUAUTO_STATE_DIR=STATE, NUAUTO_DEMO="1", NUAUTO_DEMO_PACE="0.02", NUAUTO_DEMO_HEADLESS="1")

from playwright.sync_api import sync_playwright  # noqa: E402

from nuauto import config  # noqa: E402
from nuauto import demo  # noqa: E402
from nuauto import jobs  # noqa: E402
from nuauto import onboard  # noqa: E402
from nuauto import sheet  # noqa: E402

PY = sys.executable
ROOT = config.PROJECT_DIR
demo.setup(["fresh"])
config.LOCAL = json.load(open(config.LOCAL_CONFIG_PATH))
config.SHEET_ID = ""


def refused(fn, needle=""):
    try:
        fn()
    except onboard.Refused as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError("expected Refused")


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# the Google client file: only a Desktop-app client, saved private
refused(lambda: onboard.check_client({"web": {"client_id": "x"}}), "Desktop app")
refused(lambda: onboard.check_client({"something": 1}), "installed")
refused(lambda: onboard.check_client({"installed": {"client_id": "x", "client_secret": "s"}}), "client ID")
refused(lambda: onboard.save_client("not json"), "JSON")
refused(lambda: onboard.save_client("x" * 30000), "too big")
good = {"installed": {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "s", "redirect_uris": ["http://localhost"]}}
onboard.save_client(json.dumps(good))
client_path = os.path.join(config.LOCAL_DIR, "client_secret.json")
assert mode(client_path) == 0o600 and onboard.client_status() == (True, None)
os.remove(client_path)

# the sheet's address -> its ID
assert onboard.sheet_id_from("https://docs.google.com/spreadsheets/d/1AbC_d-EfGhIjKlMnOp/edit#gid=0") == "1AbC_d-EfGhIjKlMnOp"
assert onboard.sheet_id_from("  1AbC_d-EfGhIjKlMnOp ") == "1AbC_d-EfGhIjKlMnOp"
refused(lambda: onboard.sheet_id_from("my sheet"), "address")

# setting up a sheet: an empty one gets the columns; one with NUauto's columns is fine; anything else is refused
refused(lambda: onboard.open_and_prepare("DEMO-SHEET"), "Log in to Google")  # not logged in yet
demo.google_login()
new = sheet.create_sheet("t")
assert onboard.open_and_prepare(new) == "set up"
ws = sheet.client(False).open_by_key(new).sheet1
assert ws.get_all_values()[0] == sheet.HEADERS
assert onboard.open_and_prepare(new) == "ready"  # set up already: left alone
other = sheet.create_sheet("other")
sheet.client(False).open_by_key(other).sheet1.update(range_name="A1:B1", values=[["Name", "Grade"]])
refused(lambda: onboard.open_and_prepare(other), "never writes over")
assert sheet.client(False).open_by_key(other).sheet1.get_all_values() == [["Name", "Grade"]]  # untouched
refused(lambda: onboard.open_and_prepare("DEMO-NOPE"), "Could not open")

# the resume: a PDF with text
SCRATCH = os.path.join(STATE, "scratch")
os.makedirs(SCRATCH)
assert onboard.resume_preview(os.path.join(SCRATCH, "nope.pdf")) == (False, "No file there.")
txt = os.path.join(SCRATCH, "resume.txt")
open(txt, "w").write("not a pdf")
assert onboard.resume_preview(txt)[0] is False
ok, text = onboard.resume_preview(os.path.join(STATE, "Demo_Student_Resume.pdf"))
assert ok and "STM32" in text, text
blank = os.path.join(SCRATCH, "blank.pdf")
open(blank, "wb").write(demo.tiny_pdf([""]))
assert "no text" in onboard.resume_preview(blank)[1]

# preferences: the defaults pass as they are, unchanged (saving the prefilled form keeps an existing setup exactly
# as it was: same pool, same prompt); each field is checked
clean = onboard.check_prefs(dict(jobs.DEFAULTS))
assert clean == jobs.DEFAULTS, {k: (clean.get(k), v) for k, v in jobs.DEFAULTS.items() if clean.get(k) != v}
for bad, needle in (({"threshold_above": 50}, "can't be lower"), ({"class_year": "middler"}, "Your year"),
                    ({"term_id": "x!"}, "Term ID"), ({"category_bonus": {"cooking": 5}}, "unknown category"),
                    ({"student": "{{student}}"}, "student"), ({"threshold": "lots"}, "whole number"),
                    ({"home_state": "Mass"}, "two letters"), ({"rank_last": ["nope"]}, "Rank last"),
                    ({"tags": [{"name": "", "bonus": 5, "phrases": ["x"]}]}, "needs a name"),
                    ({"tags": [{"name": "cars", "bonus": 5, "phrases": " , "}]}, "one or more phrases"),
                    ({"tags": [{"name": "cars", "bonus": 99, "phrases": "EV"}]}, "between -50 and 50"),
                    ({"tags": [{"name": "cars", "bonus": 1, "phrases": "EV"}] * 2}, "twice")):
    refused(lambda: onboard.check_prefs({**jobs.DEFAULTS, **bad}), needle)
assert onboard.check_prefs({**jobs.DEFAULTS, "major_words": "Computer Science, , Khoury"})["major_words"] == ["computer science", "khoury"]
assert onboard.check_prefs({**jobs.DEFAULTS, "tags": [{"name": " cars ", "bonus": "7", "phrases": "automotive, , EV"},
                                                    {"name": "", "bonus": "", "phrases": ""}]})["tags"] == \
    [{"name": "cars", "bonus": 7, "phrases": ["automotive", "EV"]}]
onboard.save_config({"preferences": {**clean, "class_year": "junior", "term": "2027 - Fall"}})
assert jobs.MY_YEAR == 2 and jobs.TERM_TEXT == "fall 2027" and json.load(open(config.LOCAL_CONFIG_PATH))["preferences"]["class_year"] == "junior"
onboard.save_config({"preferences": None})
jobs.set_prefs(None)

# the term list: found wherever it sits in NUworks' JSON, sorted, non-terms ignored
found = onboard.find_terms({"a": [{"_id": "x1", "_label": "2027 - Spring"}, {"_id": "j", "_label": "Co-op"}],
                            "b": {"deep": [{"value": 7, "label": "2026 - Fall"}, {"id": "z", "title": "2027 - Fall "}]}})
assert found == [{"id": "7", "label": "2026 - Fall"}, {"id": "x1", "label": "2027 - Spring"}, {"id": "z", "label": "2027 - Fall"}], found

# Discord: a webhook URL only, saved private
refused(lambda: onboard.save_webhook("https://example.com/hook"), "Discord webhook")
onboard.save_webhook("https://discord.com/api/webhooks/1/abc")
assert mode(config.DISCORD_WEBHOOK_PATH) == 0o600
os.remove(config.DISCORD_WEBHOOK_PATH)

# automatic updates (demo: unit files go to the demo folder, systemctl / launchctl are never called)
if onboard.scheduler_kind():
    onboard.scheduler_enable()
    assert onboard.scheduler_enabled() and all(p.startswith(STATE) for p in onboard.scheduler_paths())
    service = [p for p in onboard.scheduler_paths() if p.endswith(".service")]
    if service:
        assert f"ExecStart={onboard.nuauto_bin()} daily" in open(service[0]).read()
    onboard.scheduler_disable()
    assert not onboard.scheduler_enabled()

# the steps on a fresh start: everything to do but the tools
config.SHEET_ID = ""
steps = {s["id"]: s for s in onboard.steps({})}
assert list(steps) == ["welcome", "tools", "google_client", "google_login", "sheet", "resume", "nuworks", "preferences", "extras"]
assert steps["welcome"]["status"] == "todo" and steps["extras"]["status"] == "optional" and not onboard.complete(list(steps.values()))
os.remove(config.TOKEN_PATH)


# ---------------------------------------------------------------- the whole wizard, in the browser

ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}
GUI_ERR = os.path.join(STATE, "gui-stderr.log")  # a file, not a pipe nobody reads (a full pipe would freeze the server)
gui = subprocess.Popen([PY, "-m", "nuauto", "gui", "--no-open"], env=ENV, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=open(GUI_ERR, "w"), text=True)
try:
    url = json.loads(gui.stdout.readline())["url"]
    client_file = os.path.join(SCRATCH, "client_secret_123.json")
    json.dump(good, open(client_file, "w"))
    errors = []

    def done(page, step, timeout=60000):
        try:
            page.wait_for_selector(f"[data-testid=step-{step}].done", timeout=timeout)
        except Exception:  # say why: the step as the server sees it, the messages on screen, the server's errors
            try:
                steps_now = page.evaluate("() => fetch('/api/setup', { signal: AbortSignal.timeout(10000) })"
                                          ".then((r) => r.json())")["steps"]
                now = next(s for s in steps_now if s["id"] == step)
                seen = f"{now['status']}: {now['detail']}"
            except Exception as e:
                seen = f"no answer from the server ({type(e).__name__})"
            raise AssertionError(f"step {step} not done after {timeout // 1000}s ({seen}); on screen: "
                                 f"{page.locator('[data-testid=toast]').all_inner_texts()}; page errors: {errors}; "
                                 f"server errors: {open(GUI_ERR).read()[-2000:] or 'none'}") from None

    with sync_playwright() as p:
        b = p.firefox.launch(headless=True)
        page = b.new_page(viewport={"width": 1200, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_selector("[data-testid=setup-steps]")  # a fresh start opens Setup
        page.click("[data-testid=setup-ack]")  # the box is not ticked: refused
        page.wait_for_selector("[data-testid=toast]")
        page.check("[data-testid=setup-tos]")
        page.click("[data-testid=setup-ack]")
        done(page, "welcome")
        done(page, "tools", 30000)
        page.set_input_files("[data-testid=setup-client-file]", client_file)
        done(page, "google_client")
        page.click("[data-testid=setup-google-login]")
        done(page, "google_login")
        page.click("[data-testid=setup-sheet-create]")
        done(page, "sheet")
        page.click("[data-testid=setup-resume-pick]")
        done(page, "resume")
        page.click("[data-testid=setup-nuworks-login]")
        # Read my resumes the moment the login ends: the NUworks check the login starts is still running, gives the
        # browser up to the read, and must run again after it (else the step stays "to do": seen in CI)
        for _ in range(600):
            task = page.evaluate("() => fetch('/api/task?after=0').then((r) => r.json())")["task"]
            if task and task["kind"] == "login_nuworks" and task["state"] != "running":
                break
            page.wait_for_timeout(100)
        page.click("[data-testid=setup-labels-read]")
        page.wait_for_selector("input[type=radio][name=label]", timeout=60000)
        page.check("input[type=radio][name=label]")
        page.click("[data-testid=setup-label-use]")
        done(page, "nuworks", 60000)
        page.click("[data-testid=pref-terms-read]")
        page.wait_for_selector("[data-testid=pref-term-select]", timeout=60000)
        page.select_option("[data-testid=pref-term-select]", label="2027 - Fall")
        page.select_option("[data-testid=pref-class_year]", "junior")
        page.click("details summary")  # fine-tuning: a new tag, the wearables one removed
        assert page.locator("[data-testid=pref-tags] tr").nth(1).locator("[aria-label='Tag name']").input_value() == "wearables"
        page.locator("[data-testid=pref-tags] tr").nth(1).get_by_text("Remove").click()
        page.click("[data-testid=pref-tag-add]")
        row = page.locator("[data-testid=pref-tags] tr").last
        row.locator("[aria-label='Tag name']").fill("cars")
        row.locator("[aria-label='Tag phrases']").fill("automotive, EV")
        row.locator("[aria-label='Tag bonus']").fill("7")
        page.click("[data-testid=pref-save]")
        done(page, "preferences")
        page.wait_for_selector("[data-testid=setup-finish]", timeout=30000)
        page.click("[data-testid=setup-finish]")
        page.wait_for_selector("[data-testid=week]")
        page.wait_for_function("() => !document.body.innerText.includes('Finish setting up')", timeout=10000)
        b.close()
    assert not errors, errors
    cfg = json.load(open(config.LOCAL_CONFIG_PATH))
    assert cfg["tos_ack"] and cfg["sheet_id"].startswith("DEMO-") and cfg["resume_path"].endswith("Demo_Student_Resume.pdf")
    assert cfg["preferences"]["term"] == "2027 - Fall" and cfg["preferences"]["class_year"] == "junior"
    assert cfg["preferences"]["term_id"] == "demo00000000000000000000fall2027"
    assert [t["name"] for t in cfg["preferences"]["tags"]] == ["AR/XR", "cars"]
    assert cfg["preferences"]["tags"][1] == {"name": "cars", "bonus": 7, "phrases": ["automotive", "EV"]}
    assert json.load(open(config.PROFILE_PATH))["resume_label"] == demo.RESUME_LABEL
    assert mode(client_path) == 0o600 and mode(config.LOCAL_DIR) == 0o700
    values = sheet.client(False).open_by_key(cfg["sheet_id"]).sheet1.get_all_values()
    assert values[0] == sheet.HEADERS
finally:
    gui.terminate()
    gui.wait(timeout=30)

print("All setup checks passed.")

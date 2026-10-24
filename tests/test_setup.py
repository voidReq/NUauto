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
from nuauto import fields  # noqa: E402
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
# what you study: every field's starting values pass the checks and fill both prompts completely
for key in fields.FIELDS:
    got = onboard.check_prefs({**jobs.DEFAULTS, **fields.preset(key), "field": key})
    assert got["field"] == key and got["categories"][-1]["key"] == "other", key
    assert [c["key"] for c in got["categories"]] == [c["key"] for c in fields.FIELDS[key]["categories"]], key
    jobs.set_prefs(got)
    for name in ("TRIAGE_PROMPT.md", "CATEGORY_PROMPT.md"):
        text = open(jobs.render_prompt(name)).read()
        assert "{{" not in text and (name != "CATEGORY_PROMPT.md" or all(f"`{c}`" in text for c in jobs.CATEGORIES)), (key, name)
    assert jobs.RATE_ORDER[-1] == "uncategorized" and set(jobs.RATE_ORDER) == set(jobs.CATEGORIES) | {"uncategorized"}
    assert jobs.category_label(jobs.CATEGORIES[0]) == fields.FIELDS[key]["categories"][0]["label"]
jobs.set_prefs(None)
assert jobs.CATEGORIES[0] == "security" and jobs.RATE_ORDER[-2:] == ["frontend", "uncategorized"]
# kinds of work you write yourself: a key from the name, Other added, each one checked
mine = onboard.check_prefs({**jobs.DEFAULTS, "category_bonus": {}, "category_threshold": {}, "rank_last": [],
                            "categories": [{"label": "Data Journalism!", "description": "newsroom data work"}, {"label": "", "description": ""}]})
assert mine["categories"] == [{"key": "data_journalism", "label": "Data Journalism!", "description": "newsroom data work"},
                              {"key": "other", "label": "Other", "description": "anything else."}], mine["categories"]
for cats, needle in (([{"label": "A", "description": "x"}, {"label": "a", "description": "y"}], "twice"),
                     ([{"label": "A", "description": ""}], "say in a line"),
                     ([{"label": "123", "description": "x"}], "starts with a letter"),
                     ([{"label": "A", "description": "use {{student}}"}], "say in a line"),
                     ([], "up to 25"), ("nope", "up to 25")):
    refused(lambda: onboard.check_prefs({**jobs.DEFAULTS, "category_bonus": {}, "category_threshold": {}, "rank_last": [],
                                         "categories": cats}), needle)
refused(lambda: onboard.check_prefs({**jobs.DEFAULTS, "field": "astrology"}), "What you study")
refused(lambda: onboard.check_prefs({**jobs.DEFAULTS, **fields.preset("business"), "category_bonus": {"security": 5}}), "unknown category")
# a job sorted into another set of kinds (before you changed field, or before the kinds changed) counts as not sorted:
# it is sorted again. One from before sets were recorded was sorted into the engineering kinds of 2026-10-10 (LEGACY).
eng = fields.fingerprint(fields.ENGINEERING_CATEGORIES)
cats = {"a": "security", "b": "finance", "c": "finance", "d": "security"}
sets = {"c": fields.fingerprint(fields.FIELDS["business"]["categories"]), "d": eng}
assert [jobs.job_category(cats, sets, i) for i in "abcd"] == ["uncategorized", "uncategorized", "uncategorized", "security"]
old = [c for c in fields.ENGINEERING_CATEGORIES if c["key"] != "frontend"]
assert fields.fingerprint(old) == fields.LEGACY != eng
jobs.set_prefs({"categories": old, "category_bonus": {}, "rank_last": []})  # kept the old kinds: nothing to sort again
assert [jobs.job_category(cats, sets, i) for i in "ad"] == ["security", "uncategorized"]
jobs.set_prefs({**fields.preset("business"), "field": "business"})
assert [jobs.job_category(cats, sets, i) for i in "abc"] == ["uncategorized", "uncategorized", "finance"]
jobs.set_prefs(None)
onboard.save_config({"preferences": {**clean, "class_year": "junior", "term": "2027 - Fall"}})
assert jobs.MY_YEAR == 2 and jobs.TERM_TEXT == "fall 2027" and json.load(open(config.LOCAL_CONFIG_PATH))["preferences"]["class_year"] == "junior"
onboard.save_config({"preferences": None})
jobs.set_prefs(None)
# a job dropped by the first triage (an update before you said what you study: the engineering defaults) is triaged
# again once the triage text changes; kept ones and decisions from before "by" was recorded stay
eng = jobs.triage_set()
triage = {"a": {"keep": False, "by": eng}, "b": {"keep": True, "by": eng}, "c": {"keep": False}}
assert [jobs.needs_triage(triage, i) for i in "abcd"] == [False, False, False, True]
jobs.set_prefs({**fields.preset("health"), "field": "health"})
assert jobs.triage_set() != eng and [jobs.needs_triage(triage, i) for i in "abcd"] == [True, False, False, True]
jobs.set_prefs(None)
# the resume-label read needs one NUworks job: the pool's best; with no pool (a new student's can be empty), any job
# with details that applies on NUworks, then any listed one (demo mode adds its own job last)
demo_job = [jobs.job_url("900101")]
jobs.save("list.json", {"l1": {}, "l2": {}})
assert onboard.a_job_urls() == [jobs.job_url("l1"), jobs.job_url("l2")] + demo_job
jobs.save("pool.json", [{"id": "p1", "flags": ["apply on company site too?"]}, {"id": "p2", "flags": []}])
assert onboard.a_job_urls()[:3] == [jobs.job_url("p2"), jobs.job_url("p1"), jobs.job_url("l1")]
os.remove(jobs.path("pool.json")); os.remove(jobs.path("list.json"))
assert onboard.a_job_urls() == demo_job

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
assert list(steps) == ["welcome", "tools", "google_client", "google_login", "sheet", "resume", "nuworks", "preferences", "extras",
                      "internships", "password_manager", "homelab"]
assert steps["welcome"]["status"] == "todo" and steps["extras"]["status"] == "optional" and not onboard.complete(list(steps.values()))
# the welcome step names the limits the code enforces: the weekly one from Settings (max_per_week), not a fixed 11
from nuauto import sheet  # noqa: E402
config.LOCAL["max_per_week"] = 5
assert next(s for s in onboard.steps({}) if s["id"] == "welcome")["data"] == {"week": 5, "total": sheet.MAX_TOTAL}
config.LOCAL.pop("max_per_week")
os.remove(config.TOKEN_PATH)

# the optional steps at the end: Password manager always; Assistant on your homelab only with a homelab (not on the
# homelab itself, not in the packaged app); optional steps never keep the wizard from finishing
from nuauto import accounts  # noqa: E402
from nuauto import health  # noqa: E402

assert list(steps)[-2] == "password_manager" and steps["password_manager"]["status"] == "optional"
assert "homelab_assistant" not in steps and not onboard.homelab_assistant()["available"]  # demo: no homelab
# a homelab: optional, there in a source install (never the packaged app: it is always local mode), not used yet
assert list(steps)[-1] == "homelab" and steps["homelab"]["status"] == "optional"
hl = steps["homelab"]["data"]
assert hl["available"] and not hl["on"] and not hl["saved"] and hl["default_dir"] == "projects/auto"
# what it checks and saves: names and folders only like the ones it can pass to ssh; its page never on every address
assert not onboard.homelab_probe("bad name; rm -rf")["ok"] and not onboard.homelab_probe("-oProxyCommand=x")["ok"]
assert not onboard.homelab_probe("homelab", "../etc")["ok"] and not onboard.homelab_probe("homelab", "/abs")["ok"]
for bad in (("homelab", "lab", "projects/auto", "0.0.0.0"), ("homelab", "lab", "projects/auto", "127.0.0.1"),
            ("homelab", "lab", "projects/auto", "not-an-ip"), ("x y", "lab", "projects/auto", "100.64.0.2"),
            ("homelab", "lab", "../up", "100.64.0.2")):
    try:
        onboard.homelab_save(*bad)
    except onboard.Refused:
        pass
    else:
        raise AssertionError(f"saved {bad}")
saved_cfg = dict(onboard.local_config())
link = onboard.homelab_save("homelab", "lab-box", "projects/auto", "100.64.0.2")
assert link["saved"] and link["ssh"] == "homelab" and link["hostname"] == "lab-box" and link["listen"] == "100.64.0.2"
assert not link["on"]                                                       # (this process: after a restart of NUauto)
assert not onboard.homelab_save("", "", "", "")["saved"]                     # stop using it: local mode again
onboard.save_config({k: saved_cfg.get(k, "") for k in ("server_hostname", "server_ssh", "server_dir", "web_listen_host")})
accounts.PATH = os.path.join(config.LOCAL_DIR, "accounts.json")
with open(accounts.PATH, "w") as f:
    json.dump({"a.example.com": {"password": "pw-secret-a"}, "b.example.com": {"exported": True},
               "c.example.com": {"own": True}, "d.example.com": {"own": True, "exported": True}}, f)
pm = onboard.password_manager({})
assert pm == {"on": False, "check": None, "waiting": 1, "own": 2, "only": ["myworkday"], "bw": False}, pm  # demo: never a real bw
assert "pw-secret" not in json.dumps(steps)
old_pm = {k: onboard.local_config().get(k) for k in ("password_manager", "bitwarden_import", "remote_assist", "remote_auto",
                                                    "assist_slots", "remote_hours")}


def chk(status):
    return {"bitwarden": health.check("bitwarden", "Bitwarden", status, "x")}


def step(id_, checks=None):
    return next(s for s in onboard.steps(checks or {}) if s["id"] == id_)


try:
    onboard.save_config({"password_manager": "bitwarden", "bitwarden_import": ["amazon.jobs", "myworkday"]})
    assert step("password_manager", chk("ok"))["status"] == "done"      # on and the check is OK: done
    for status in ("warn", "fail", "unknown"):
        assert step("password_manager", chk(status))["status"] == "optional", status
    assert step("password_manager")["status"] == "optional"             # no check result yet
    pm = step("password_manager", chk("warn"))["data"]
    assert pm["on"] and pm["only"] == ["amazon.jobs", "myworkday"] and pm["check"]["status"] == "warn", pm
    onboard.save_config({"password_manager": ""})
    assert step("password_manager", chk("ok"))["status"] == "optional"  # off, even when the check says ok
    onboard.save_config({"password_manager": "bitwarden", "bitwarden_import": []})
    assert onboard.password_manager({})["only"] == ["myworkday"]        # empty: the default
    # the homelab step: one step, which homelab, then (with one) NUauto there: its page and its assistant
    real_flags = (config.HAS_SERVER, config.IS_SERVER, config.FROZEN)
    try:
        config.HAS_SERVER, config.IS_SERVER, config.FROZEN = True, False, False
        ha = onboard.homelab_assistant()
        assert ha["available"] and ha["on"] is False and ha["auto"] is False and ha["review_url"] is None, ha
        assert ha["slots"] == 3 and ha["hours"] == list(__import__("nuauto.remote", fromlist=["x"]).hours()), ha
        ids = [s["id"] for s in onboard.steps({})]
        assert ids[-2:] == ["password_manager", "homelab"] and "homelab_assistant" not in ids, ids
        assert step("homelab")["status"] == "done" and step("homelab")["data"]["assistant"] == ha
        assert ha["app"] is False                                          # NUauto's page there: off too
        onboard.save_config({"remote_app": True})                          # its page alone (no assistant)
        assert onboard.homelab_assistant()["app"] and not onboard.homelab_assistant()["on"]
        assert step("homelab")["data"]["assistant"]["app"] is True
        onboard.save_config({"remote_app": False})
        onboard.save_config({"remote_assist": True, "remote_auto": True, "assist_slots": 2, "remote_hours": [8, 20]})
        ha = onboard.homelab_assistant()
        assert ha["on"] and ha["app"] and ha["auto"] and ha["slots"] == 2 and ha["hours"] == [8, 20], ha   # (the assistant needs the page)
        assert onboard.homelab_assistant(here=True)["available"]          # the homelab's own page: its queue's settings
        assert ha["review_url"] is None or (ha["review_url"].startswith("http") and not ha["review_url"].endswith("/")), ha
        for bad in (0, 4, "2", None):  # (True also passes isinstance int: onboard.py homelab_assistant, minor)
            onboard.save_config({"assist_slots": bad})
            assert onboard.homelab_assistant()["slots"] == 3, bad        # out of range: the default
        config.IS_SERVER = True                                          # the homelab itself: nothing of it to set there
        assert not onboard.homelab_assistant()["available"]
        assert step("homelab")["data"]["assistant"] is None
        config.IS_SERVER, config.FROZEN = False, True                    # the packaged app: always local mode, no step
        assert not onboard.homelab_assistant()["available"]
        assert "homelab" not in [s["id"] for s in onboard.steps({})]
        config.FROZEN, config.HAS_SERVER = False, False
        assert not onboard.homelab_assistant()["available"] and step("homelab")["data"]["assistant"] is None
    finally:
        config.HAS_SERVER, config.IS_SERVER, config.FROZEN = real_flags
    # optional steps (todo ones done) never block complete(); a todo one still does
    every = [{"id": "x", "status": "done"}, {"id": "password_manager", "status": "optional"},
             {"id": "homelab", "status": "optional"}]
    assert onboard.complete(every) and not onboard.complete(every + [{"id": "y", "status": "todo"}])
finally:
    onboard.save_config({k: v for k, v in old_pm.items() if v is not None})
    cfg_now = onboard.local_config()
    for k, v in old_pm.items():
        if v is None:
            cfg_now.pop(k, None)
    onboard.write_json(config.LOCAL_CONFIG_PATH, cfg_now, mode=0o644)
    config.LOCAL = cfg_now
    os.remove(accounts.PATH)


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
        # the list ends with the optional Password manager and Homelab steps; no homelab, so no homelab-assistant step
        assert page.locator("[data-testid=setup-steps] > li").last.get_attribute("data-testid") == "step-homelab"
        assert "optional" in page.locator("[data-testid=step-password_manager] .head").inner_text().lower()
        assert page.locator("[data-testid=step-homelab_assistant]").count() == 0
        # the Homelab step: Check asks it over ssh (the demo answers with made-up facts) and offers to use it
        page.click("[data-testid=step-homelab] .head")
        page.fill("[data-testid=hl-ssh]", "homelab")
        page.click("[data-testid=hl-check]")
        page.wait_for_selector("[data-testid=hl-facts]")
        assert "100.64.0.42" in page.inner_text("[data-testid=hl-facts]") and "demo-homelab" in page.inner_text("[data-testid=hl-facts]")
        assert page.is_enabled("[data-testid=hl-use]")
        page.click("[data-testid=step-homelab] .head")  # closed again (nothing saved: the wizard goes on from the top)
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
        page.locator("[data-testid=setup-resume-found]").first.click()  # one the demo has: NUauto keeps a copy
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
        # what you study: a first setup picks it before saving; Business fills in its starting values (term, year and
        # tags stay; no confirm, nothing of yours to replace yet); its kinds of work show
        assert page.locator("[data-testid=pref-field] [aria-pressed=true]").count() == 0
        page.wait_for_selector("[data-testid=prefs-defaults]")
        page.click("[data-testid=pref-save]")
        page.wait_for_selector("[data-testid=toast].error:has-text('Pick what you study first')")
        assert "done" not in (page.get_attribute("[data-testid=step-preferences]", "class") or "")
        page.click("[data-testid=pref-field-business]")
        page.wait_for_selector("[data-testid=pref-field-business][aria-pressed=true]")
        page.wait_for_selector("[data-testid=prefs-picked]")
        page.click("[data-testid=pref-field-health]")  # switching again asks first: Cancel keeps Business
        page.click(".modal button:has-text('Cancel'), [role=dialog] button:has-text('Cancel')")
        assert page.get_attribute("[data-testid=pref-field-business]", "aria-pressed") == "true"
        assert "D'Amore-McKim" in page.input_value("[data-testid=pref-student]")
        assert page.input_value("[data-testid=pref-class_year]") == "junior"
        page.click("[data-testid=pref-kinds-box] summary")
        assert page.locator("[data-testid=pref-kinds] [aria-label='Kind of work']").first.input_value() == "Finance"
        page.click("[data-testid=pref-kind-add]")
        new = page.locator("[data-testid=pref-kinds] tr").nth(-2)  # added above Other
        new.locator("[aria-label='Kind of work']").fill("Real estate")
        new.locator("textarea").fill("property management, real estate finance and development.")
        new.locator("[aria-label='Real estate bonus'], [aria-label=' bonus']").first.fill("5")
        page.click("summary:has-text('Fine-tuning')")  # fine-tuning: a new tag, the wearables one removed
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
    assert cfg["tos_ack"] and cfg["sheet_id"].startswith("DEMO-") and cfg["resume_path"] == os.path.join(config.LOCAL_DIR, "resume.pdf")
    assert cfg["preferences"]["term"] == "2027 - Fall" and cfg["preferences"]["class_year"] == "junior"
    assert cfg["preferences"]["term_id"] == "demo00000000000000000000fall2027"
    assert [t["name"] for t in cfg["preferences"]["tags"]] == ["AR/XR", "cars"]
    keys = [c["key"] for c in cfg["preferences"]["categories"]]
    assert cfg["preferences"]["field"] == "business" and keys[0] == "finance" and keys[-2:] == ["real_estate", "other"], keys
    assert cfg["preferences"]["category_bonus"] == {"real_estate": 5}, cfg["preferences"]["category_bonus"]
    assert cfg["preferences"]["tags"][1] == {"name": "cars", "bonus": 7, "phrases": ["automotive", "EV"]}
    assert json.load(open(config.PROFILE_PATH))["resume_label"] == demo.RESUME_LABEL
    assert mode(client_path) == 0o600 and mode(config.LOCAL_DIR) == 0o700
    values = sheet.client(False).open_by_key(cfg["sheet_id"]).sheet1.get_all_values()
    assert values[0] == sheet.HEADERS
finally:
    gui.terminate()
    gui.wait(timeout=30)

print("All setup checks passed.")

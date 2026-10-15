"""Offline checks for Settings' homelab-assistant and password-manager keys (gui.settings_put / settings_get /
apply_config / remote_changed). Run: python tests/test_homelab_settings.py

In this process, on a temp folder: no ssh, no network, no real Bitwarden. sync.push and sync.remote are recorded, the
GUI's health runner is a stand-in, and the homelab is switched on and off by config flags.
"""
import json
import os
import tempfile
import time

STATE = tempfile.mkdtemp(prefix="nuauto-test-hlsettings-")
os.environ["NUAUTO_STATE_DIR"] = STATE

from nuauto import config  # noqa: E402

assert config.LOCAL_DIR == os.path.join(STATE, "local") and not config.DEMO
os.makedirs(config.LOCAL_DIR, mode=0o700, exist_ok=True)

from nuauto import gui, remote, sync  # noqa: E402


class FakeApp:
    """Stands in for gui.APP: records which check groups a settings change reruns; no real checks run."""

    def __init__(self):
        self.ran = []

    def run_now(self, groups):
        self.ran.append(list(groups))

    def checks(self):
        return []


gui.APP = FakeApp()
pushed, restarted = [], []
sync.push = lambda code=None: pushed.append(1) or True
sync.remote = lambda command, what: restarted.append(command) or True
with open(config.LOCAL_CONFIG_PATH, "w") as f:
    json.dump({"web_listen_host": "100.64.0.9"}, f)


def saved():
    return json.load(open(config.LOCAL_CONFIG_PATH))


def refused(body, needle=""):
    before = (saved(), len(pushed), len(restarted), list(gui.APP.ran))
    try:
        gui.settings_put(body)
    except gui.Refused as e:
        assert needle in str(e), (body, str(e))
    else:
        raise AssertionError(f"expected Refused: {body}")
    assert before == (saved(), len(pushed), len(restarted), gui.APP.ran), body  # nothing saved, pushed or rerun


def settle(pushes, restarts=None):
    """The push runs in a background thread: wait for it (and its restart)."""
    end = time.time() + 10
    while time.time() < end:
        if len(pushed) >= pushes and (restarts is None or len(restarted) >= restarts) and gui.APP.ran[-1:] == [["homelab"]]:
            break
        time.sleep(0.02)
    assert len(pushed) == pushes and (restarts is None or len(restarted) == restarts), (pushed, restarted)
    time.sleep(0.1)  # a stray extra push or restart would show now
    assert len(pushed) == pushes and (restarts is None or len(restarted) == restarts), (pushed, restarted)


def homelab(on):
    config.HAS_SERVER, config.IS_SERVER, config.FROZEN, config.DEMO = on, False, False, False


# ---- no homelab: every remote key is refused, whatever its value, and nothing is saved
homelab(False)
for body in ({"remote_assist": True}, {"remote_auto": False}, {"remote_hours": [9, 22]}, {"remote_app": True},
             {"remote_assist": True, "resume_label": "x"}):
    refused(body, "needs a homelab")
assert gui.settings_get()["homelab_assistant"]["available"] is False
# the packaged app and the homelab itself have none either
config.HAS_SERVER, config.IS_SERVER = True, True
refused({"remote_assist": True}, "needs a homelab")
config.IS_SERVER, config.FROZEN = False, True
refused({"remote_hours": [9, 22]}, "needs a homelab")
config.FROZEN = False
# assistants at once: the laptop's own sessions side by side too, so no homelab needed (2026-10-14)
for bad in (0, 4, "2", True):
    refused({"assist_slots": bad}, "1, 2 or 3")
assert "assist_slots" not in saved()

# ---- with a homelab: what is accepted
homelab(True)
assert gui.settings_get()["homelab_assistant"]["available"] is True
for bad in ("yes", 1, 0, None, "true", [True]):
    refused({"remote_assist": bad}, "on or off")
    refused({"remote_auto": bad}, "on or off")
for bad in ([22, 9], [9, 9], [-1, 5], [0, 25], [1, 2, 3], [9], [], "9-22", None, [9.5, 12], [True, 12], ["9", "12"], {"a": 1}, 9):
    refused({"remote_hours": bad}, "Hours")
for bad in (0, 4, -1, "2", 2.0, True, False, None, [2]):
    refused({"assist_slots": bad}, "1, 2 or 3")
# one bad key in a body saves none of it (and the others' good values are not written)
refused({"password_manager": "bitwarden", "assist_slots": 9}, "1, 2 or 3")
refused({"remote_assist": True, "remote_hours": [9, 9]}, "Hours")
refused({"password_manager": "keepass"}, "Bitwarden")
refused({"bitwarden_import": ["ok.site", "no good"]}, "site text")
refused({"bitwarden_import": "myworkday"}, "site text")
assert not any(k in saved() for k in ("remote_assist", "password_manager", "assist_slots", "remote_hours"))

# ---- turning it on: saved, this process's config follows, the homelab gets the settings and a web restart
assert gui.APP.ran == [] and pushed == [] and restarted == []
r = gui.settings_put({"remote_assist": True})
assert saved()["remote_assist"] is True and config.REMOTE_ASSIST is True
assert r["homelab_assistant"]["on"] is True and r["homelab_assistant"]["auto"] is False and r["homelab_assistant"]["slots"] == 3
assert r["homelab_assistant"]["hours"] == list(remote.hours())  # the default window until you set one
assert ["quick"] in gui.APP.ran and ["quick", "bitwarden"] not in gui.APP.ran  # no password manager key: no Bitwarden recheck
settle(1, 1)
assert restarted == ["systemctl --user restart nuauto-web"]
# the other keys push, but the web page restarts only when remote_assist itself flipped
r = gui.settings_put({"remote_auto": True})
assert saved()["remote_auto"] is True and r["homelab_assistant"]["auto"] is True
assert r["homelab_assistant"]["auto_tabs"] == {"nuworks": True, "other": True}
settle(2, 1)
r = gui.settings_put({"remote_auto": {"nuworks": False}})                       # one tab: the other stays as it was
assert saved()["remote_auto"] == {"nuworks": False, "other": True}
assert r["homelab_assistant"]["auto"] is True and r["homelab_assistant"]["auto_tabs"] == {"nuworks": False, "other": True}
settle(3, 1)
r = gui.settings_put({"remote_auto": {"other": False}})
assert saved()["remote_auto"] == {"nuworks": False, "other": False} and r["homelab_assistant"]["auto"] is False
for bad in ({}, {"nuworks": 1}, {"elsewhere": True}):
    refused({"remote_auto": bad}, "on or off")
settle(4, 1)
r = gui.settings_put({"remote_auto": True})
settle(5, 1)
r = gui.settings_put({"remote_assist": True, "remote_hours": [8, 20], "assist_slots": 2})  # on -> on
assert saved()["remote_hours"] == [8, 20] and saved()["assist_slots"] == 2
assert (r["homelab_assistant"]["hours"], r["homelab_assistant"]["slots"]) == ([8, 20], 2), r["homelab_assistant"]
settle(6, 1)
pushes = 6
for good in (1, 3):
    assert gui.settings_put({"assist_slots": good})["homelab_assistant"]["slots"] == good
    pushes += 1
    settle(pushes, 1)
for hours in ([0, 24], [23, 24]):
    assert gui.settings_put({"remote_hours": hours})["homelab_assistant"]["hours"] == hours
    pushes += 1
    settle(pushes, 1)
# off again: the restart happens (the page stops), config follows
r = gui.settings_put({"remote_assist": False})
assert saved()["remote_assist"] is False and config.REMOTE_ASSIST is False and r["homelab_assistant"]["on"] is False
assert r["homelab_assistant"]["review_url"] is None
pushes += 1
settle(pushes, 2)
assert restarted == ["systemctl --user restart nuauto-web"] * 2, restarted
# remote_assist true in the file but no homelab: this process does not treat it as on
homelab(False)
gui.apply_config({"remote_assist": True})
assert config.REMOTE_ASSIST is False
homelab(True)
gui.apply_config({"remote_assist": True})
assert config.REMOTE_ASSIST is True
gui.apply_config({})
assert config.REMOTE_ASSIST is False

# ---- remote_changed does nothing in demo, on the homelab itself, or without a homelab
time.sleep(0.2)
before = (len(pushed), len(restarted))
for flags in ((True, True, False), (True, False, True), (False, False, False)):  # has server, is server, demo
    config.HAS_SERVER, config.IS_SERVER, config.DEMO = flags
    gui.remote_changed(False)
time.sleep(0.3)
assert (len(pushed), len(restarted)) == before
homelab(False)

# ---- the password manager keys: validated, saved stripped, and Bitwarden's check rerun (not for other keys)
gui.APP.ran.clear()
r = gui.settings_put({"password_manager": "bitwarden", "bitwarden_import": [" myworkday ", "", "amazon.jobs"]})
assert saved()["password_manager"] == "bitwarden" and saved()["bitwarden_import"] == ["myworkday", "amazon.jobs"]
assert r["password_manager"]["on"] is True and r["password_manager"]["only"] == ["myworkday", "amazon.jobs"], r["password_manager"]
assert gui.APP.ran == [["quick", "bitwarden"]]
gui.settings_put({"bitwarden_import": []})
assert saved()["bitwarden_import"] == [] and gui.settings_get()["password_manager"]["only"] == ["myworkday"]  # empty: default
assert gui.APP.ran[-1] == ["quick"]
assert gui.settings_put({"password_manager": ""})["password_manager"]["on"] is False and gui.APP.ran[-1] == ["quick", "bitwarden"]
for text in ("ab", "a" * 60, "my-workday.jobs_1"):
    assert gui.settings_put({"bitwarden_import": [text]})["password_manager"]["only"] == [text]
refused({"bitwarden_import": ["a"]}, "site text")
refused({"bitwarden_import": ["a" * 61]}, "site text")
refused({"bitwarden_import": ["a;b"]}, "site text")
refused({"bitwarden_import": ["$(id)"]}, "site text")
refused({"bitwarden_import": ["a b"]}, "site text")

# the page's answers are JSON a browser can read: a NaN or infinity (a taste score, say) becomes null
nan, inf = float("nan"), float("inf")
assert gui.to_json({"a": nan, "b": [inf, -inf, 1.5], "c": {"d": nan}}) == '{"a": null, "b": [null, null, 1.5], "c": {"d": null}}'
assert gui.to_json({"x": [1, "s", None, True]}) == '{"x": [1, "s", null, true]}'

print("All homelab settings checks passed.")

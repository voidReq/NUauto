"""Offline checks for deploy.py (the homelab deploying main from GitHub). Run: python tests/test_deploy.py

A throwaway git repo stands in for GitHub and a temp folder for the homelab's project dir. systemctl, the package
reinstall, pgrep and Discord are replaced, so nothing real is touched.
"""
import os
import subprocess
import tempfile

from nuauto import config, deploy, doctor, sync

TMP = tempfile.mkdtemp()
REMOTE_DIR = os.path.join(TMP, "github")
LIVE = os.path.join(TMP, "live")
UNITS = os.path.join(TMP, "units")


def git(*args, cwd=REMOTE_DIR):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def commit(files, message):
    """Write files (path -> text; None deletes) into the fake GitHub repo and commit them on main."""
    for rel, text in files.items():
        path = os.path.join(REMOTE_DIR, rel)
        if text is None:
            os.remove(path)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    git("add", "-A")
    git("commit", "-q", "-m", message)


def write_live(rel, text):
    path = os.path.join(LIVE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read_live(rel):
    with open(os.path.join(LIVE, rel)) as f:
        return f.read()


class Fake(deploy.Deploy):
    """Deploy with the machine-touching parts recorded instead of done."""
    def __init__(self):
        super().__init__(remote="file://" + REMOTE_DIR, live=LIVE, work=os.path.join(TMP, "work"), unit_dir=UNITS,
                         state_path=os.path.join(TMP, "deployed.json"), log_path=os.path.join(TMP, "deploy.log"),
                         modules=["config", "web"])
        self.calls, self.said, self.installs, self.running, self.web_state = [], [], 0, False, "active"

    def systemctl(self, *args):
        self.calls.append(args)
        out = self.web_state if args[0] == "is-active" else ""
        return subprocess.CompletedProcess(args, 0, stdout=out + "\n", stderr="")

    def update_running(self):
        return self.running

    def install_deps(self):
        self.installs += 1

    def announce(self, text):
        self.said.append(text)


deploy.time.sleep = lambda s: None  # the restart check waits a few seconds

# ---- the fake GitHub: main with a tiny package, the old version of it already live
os.makedirs(REMOTE_DIR)
git("init", "-q", "-b", "main")
v1 = {"pyproject.toml": "[project]\nname='x'\n", "local_config.example.json": "{}\n",
      "src/nuauto/__init__.py": "", "src/nuauto/config.py": "A = 1\n", "src/nuauto/web.py": "B = 1\n",
      "src/nuauto/daily.py": "C = 1\n", "docs/DEPLOY.md": "v1\n", "tests/test_x.py": "not deployed\n",
      "deploy/systemd/nuauto-web.service": "[Service]\nExecStart=a\n"}
commit(v1, "first")
for rel, text in v1.items():
    if not rel.startswith("tests/"):
        write_live(rel, text)
os.makedirs(UNITS)
with open(os.path.join(UNITS, "nuauto-web.service"), "w") as f:
    f.write("[Service]\nExecStart=a\n")

d = Fake()
# the live code already equals main: recorded, nothing copied or restarted
assert d.run() == 0 and d.load_state()["sha"] and not d.said and ("restart", "nuauto-web.service") not in d.calls
first_sha = d.load_state()["sha"]
n = len(d.calls)
assert d.run() == 0 and len(d.calls) == n and not d.said, "the same commit again must do nothing"

# a docs-only commit: copied, no web restart, no reinstall, one Discord message, tests/ never copied
commit({"docs/DEPLOY.md": "v2\n", "tests/test_x.py": "changed\n"}, "docs only")
assert d.run() == 0
assert read_live("docs/DEPLOY.md") == "v2\n" and not os.path.exists(os.path.join(LIVE, "tests"))
assert ("restart", "nuauto-web.service") not in d.calls and d.installs == 0
assert len(d.said) == 1 and "docs only" in d.said[0], d.said
assert d.load_state()["sha"] != first_sha

# web code changed + a new module: both copied, web restarted
commit({"src/nuauto/sheet.py": "S = 1\n", "src/nuauto/web.py": "B = 2\n", "src/nuauto/newmod.py": "N = 1\n"}, "web")
assert d.run() == 0
assert read_live("src/nuauto/web.py") == "B = 2\n" and read_live("src/nuauto/newmod.py") == "N = 1\n"
assert ("restart", "nuauto-web.service") in d.calls
assert not [f for f in os.listdir(os.path.join(LIVE, "src/nuauto")) if f.endswith(".deploy-new")]

# a module web does not use: no restart
d.calls.clear()
commit({"src/nuauto/daily.py": "C = 2\n"}, "daily only")
assert d.run() == 0 and ("restart", "nuauto-web.service") not in d.calls

# pyproject changed: the package is reinstalled, and web restarts (new dependencies)
d.calls.clear()
commit({"pyproject.toml": "[project]\nname='x'\nversion='2'\n"}, "deps")
assert d.run() == 0 and d.installs == 1 and ("restart", "nuauto-web.service") in d.calls

# unit files: a changed one is installed, units reloaded; a new timer is enabled; the web unit restarts web
d.calls.clear()
commit({"deploy/systemd/nuauto-web.service": "[Service]\nExecStart=b\n",
        "deploy/systemd/nuauto-deploy.timer": "[Timer]\nOnBootSec=2min\n"}, "units")
assert d.run() == 0
with open(os.path.join(UNITS, "nuauto-web.service")) as f:
    assert "ExecStart=b" in f.read()
assert ("daemon-reload",) in d.calls and ("enable", "--now", "nuauto-deploy.timer") in d.calls
assert ("restart", "nuauto-web.service") in d.calls
d.calls.clear()
commit({"deploy/systemd/nuauto-deploy.timer": "[Timer]\nOnBootSec=3min\n"}, "timer changed")
assert d.run() == 0 and ("restart", "nuauto-deploy.timer") in d.calls and ("restart", "nuauto-web.service") not in d.calls

# the update is running: nothing is copied and nothing is recorded; the next run deploys it
d.calls.clear()
before = d.load_state()["sha"]
commit({"docs/DEPLOY.md": "v3\n"}, "while update runs")
d.running = True
assert d.run() == 0 and read_live("docs/DEPLOY.md") == "v2\n" and d.load_state()["sha"] == before
d.running = False
assert d.run() == 0 and read_live("docs/DEPLOY.md") == "v3\n"

# a commit that does not import never reaches the live files; Discord hears about it once
good = d.load_state()["sha"]
d.said.clear()
commit({"src/nuauto/web.py": "B = (\n", "docs/DEPLOY.md": "v4\n"}, "broken")
assert d.run() == 1
assert read_live("src/nuauto/web.py") == "B = 2\n" and read_live("docs/DEPLOY.md") == "v3\n"
state = d.load_state()
assert state["sha"] == good and state["failed"] != good and "import" in state["error"], state
assert len(d.said) == 1 and "failed" in d.said[0].lower()
assert d.run() == 1 and len(d.said) == 1, "the same broken commit must not message twice"
commit({"src/nuauto/web.py": "B = 3\n"}, "fixed")  # a fix after it deploys, and the failure is forgotten
assert d.run() == 0 and read_live("src/nuauto/web.py") == "B = 3\n" and "failed" not in d.load_state()
assert read_live("docs/DEPLOY.md") == "v4\n"

# nuauto-web does not come back after the restart: every file and unit goes back
d.said.clear()
good = d.load_state()["sha"]
commit({"src/nuauto/web.py": "B = 4\n", "docs/DEPLOY.md": "v5\n",
        "deploy/systemd/nuauto-web.service": "[Service]\nExecStart=c\n"}, "web will not start")
d.web_state = "failed"
assert d.run() == 1
assert read_live("src/nuauto/web.py") == "B = 3\n" and read_live("docs/DEPLOY.md") == "v4\n"
with open(os.path.join(UNITS, "nuauto-web.service")) as f:
    assert "ExecStart=b" in f.read(), "the old unit file must be back"
assert d.load_state()["sha"] == good and len(d.said) == 1 and "failed" in d.said[0].lower()
d.web_state = "active"
assert d.run() == 0  # the same commit again, now that web starts: it deploys
assert read_live("src/nuauto/web.py") == "B = 4\n"

# GitHub not reachable: no alarm, nothing changes, tried again next time
d.remote = "file://" + os.path.join(TMP, "nowhere")
keep = d.load_state()
assert d.run() == 0 and d.load_state() == keep

# the laptop stops pushing code once the homelab deploys by itself
sent = []
real_run = sync._run
sync._run = lambda cmd, what: sent.append(what) or True
try:
    config.DEPLOY_FROM_GIT = False
    sync.push()
    assert "push code" in sent, sent
    sent.clear()
    config.DEPLOY_FROM_GIT = True
    sync.push()
    assert "push code" not in sent and "push ratings" in sent, sent  # (local config only when the file exists: not on CI)
    sent.clear()
    sync.push(code=True)
    assert "push code" in sent
finally:
    sync._run = real_run
    config.DEPLOY_FROM_GIT = False

# one list of what makes web restart, in deploy.py and in doctor (which can't import deploy: it runs on older code)
assert [os.path.basename(p) for p in deploy.WEB_CODE] == list(doctor.WEB_FILES)
# the unit files this deploy installs exist in the repo and run the installed command
for unit in ("nuauto-deploy.service", "nuauto-deploy.timer"):
    assert os.path.isfile(os.path.join(config.PROJECT_DIR, "deploy", "systemd", unit)), unit

print("All deploy checks passed.")

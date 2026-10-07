"""Homelab: deploys `main` from GitHub by itself. nuauto-deploy.timer runs `nuauto deploy` every 5 minutes.

Needs "deploy_from_git": true in local_config.json (then the laptop stops pushing code: sync.push). The repo is
public, so nothing here needs a login. Each run:
  1. asks GitHub for main's commit; the same one as the last deploy = nothing to do
  2. waits while the update (`nuauto daily`) is running; the next run tries again
  3. fetches the commit, unpacks it into work/deploy/stage and imports the modules the homelab runs from there:
     a broken commit never reaches the live files (Discord says so once; the old code keeps running)
  4. copies the changed files over the live ones (the old ones are kept, to undo), reinstalls if pyproject.toml
     changed, installs changed unit files (daemon-reload; timers restarted, new ones enabled), and restarts
     nuauto-web only when web.py or a module it uses changed. If it does not come back, everything is undone.
  5. records the commit in local/deployed.json, one line in logs/deploy.log, one Discord message
`nuauto deploy` on the laptop starts a run on the homelab now (no waiting for the timer) and shows its log.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

from nuauto import config

# What a deploy copies from the repo: these folders (all files in them) and these files. Not tests/, packaging/, .github/.
DIRS = ["src/nuauto", "prompts", "docs", "deploy/systemd"]
FILES = ["pyproject.toml", "README.md", "CLAUDE.md", "CONTRIBUTING.md", "local_config.example.json"]
UNIT_SRC = "deploy/systemd"
# nuauto-web keeps the code it started with: restart it when one of these changed (doctor warns about the same list).
WEB_CODE = ["src/nuauto/web.py", "src/nuauto/config.py", "src/nuauto/sheet.py", "src/nuauto/jobs.py"]
# Imported from the new code before anything is copied (the modules the homelab runs; not the GUI's).
MODULES = ["cli", "config", "sheet", "jobs", "daily", "web", "doctor", "sync", "deploy", "browser", "health"]
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
STATE_PATH = os.path.join(config.LOCAL_DIR, "deployed.json")
LOG_PATH = os.path.join(config.LOGS_DIR, "deploy.log")


class DeployError(Exception):
    pass


def same(a, b):
    """Same bytes. (Not filecmp: it caches by size and mtime, and a tar's mtimes are only the commit's second.)"""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


class Deploy:
    def __init__(self, remote=None, live=None, work=None, unit_dir=UNIT_DIR, state_path=STATE_PATH,
                 log_path=LOG_PATH, modules=None):
        self.remote = remote or config.DEPLOY_REPO
        self.live = live or config.PROJECT_DIR
        self.work = work or os.path.join(config.WORK_DIR, "deploy")
        self.unit_dir, self.state_path, self.log_path = unit_dir, state_path, log_path
        self.modules = modules or MODULES
        self.repo = os.path.join(self.work, "repo.git")
        self.stage = os.path.join(self.work, "stage")
        self.undo_dir = os.path.join(self.work, "undo")
        self.python = sys.executable

    # ---- the parts a test replaces
    def systemctl(self, *args):
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=120)

    def update_running(self):
        return subprocess.run(["pgrep", "-f", "nuauto daily"], capture_output=True).returncode == 0

    def install_deps(self):
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
        cmd = [uv, "pip", "install", "--python", self.python, "-e", self.live] if os.path.exists(uv) else \
            [self.python, "-m", "pip", "install", "-e", self.live]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise DeployError("reinstalling the package failed: " + r.stderr.strip()[-300:])

    def announce(self, text):
        from nuauto import daily
        daily.discord(text)

    # ---- helpers
    def say(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")

    def git(self, *args, cwd=None):
        r = subprocess.run(["git", *args], cwd=cwd or self.work, capture_output=True, text=True, timeout=180,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode:
            raise DeployError(f"git {args[0]} failed: {r.stderr.strip()[-300:]}")
        return r.stdout.strip()

    def load_state(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_state(self, state):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump({**state, "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f)

    # ---- the steps
    def remote_sha(self):
        os.makedirs(self.work, exist_ok=True)
        out = self.git("ls-remote", self.remote, "refs/heads/main")
        if not out:
            raise DeployError("GitHub has no main branch there")
        return out.split()[0]

    def fetch(self):
        """The commit now at main, fetched (shallow) into a bare repo of ours. Returns its sha."""
        if not os.path.isdir(self.repo):
            self.git("init", "--bare", "-q", self.repo)
        self.git("fetch", "-q", "--depth", "1", self.remote, "refs/heads/main", cwd=self.repo)
        return self.git("rev-parse", "FETCH_HEAD", cwd=self.repo)

    def unpack(self, sha):
        """The deployable files of this commit in the stage folder."""
        tree = set(self.git("ls-tree", "-r", "--name-only", sha, cwd=self.repo).splitlines())
        specs = [f for f in FILES if f in tree] + [d for d in DIRS if any(p.startswith(d + "/") for p in tree)]
        shutil.rmtree(self.stage, ignore_errors=True)
        os.makedirs(self.stage)
        tar = os.path.join(self.work, "stage.tar")
        self.git("archive", "--format=tar", "-o", tar, sha, "--", *specs, cwd=self.repo)
        with tarfile.open(tar) as t:
            t.extractall(self.stage, filter="data")
        os.remove(tar)

    def check_imports(self):
        """The new code imports (first in line of the path, not the live copy), so a broken commit goes no further."""
        src = os.path.join(self.stage, "src")
        state = os.path.join(self.work, "import_state")
        os.makedirs(state, exist_ok=True)
        check = ("import importlib, os, sys\n"
                 "src = os.path.realpath(sys.argv[1])\n"
                 "for m in sys.argv[2:]:\n"
                 "    mod = importlib.import_module('nuauto.' + m)\n"
                 "    assert os.path.realpath(mod.__file__).startswith(src), 'imported the live copy: ' + mod.__file__\n")
        r = subprocess.run([self.python, "-c", check, src, *self.modules], cwd=self.stage, capture_output=True,
                           text=True, timeout=180, env={**os.environ, "PYTHONPATH": src, "NUAUTO_STATE_DIR": state,
                                                        "PYTHONDONTWRITEBYTECODE": "1"})
        if r.returncode:
            raise DeployError("the new code does not import: " + (r.stderr.strip().splitlines() or ["?"])[-1][:300])

    def changed_files(self):
        out = []
        for folder, _, names in os.walk(self.stage):
            for n in names:
                rel = os.path.relpath(os.path.join(folder, n), self.stage)
                live = os.path.join(self.live, rel)
                if not os.path.isfile(live) or not same(os.path.join(self.stage, rel), live):
                    out.append(rel)
        return sorted(out)

    def copy_in(self, changed):
        """Replace the changed files one by one (atomically), keeping the old ones for undo()."""
        shutil.rmtree(self.undo_dir, ignore_errors=True)
        for rel in changed:
            live = os.path.join(self.live, rel)
            if os.path.isfile(live):
                os.makedirs(os.path.dirname(os.path.join(self.undo_dir, rel)), exist_ok=True)
                shutil.copy2(live, os.path.join(self.undo_dir, rel))
            os.makedirs(os.path.dirname(live), exist_ok=True)
            shutil.copy2(os.path.join(self.stage, rel), live + ".deploy-new")
            os.replace(live + ".deploy-new", live)

    def undo(self, changed):
        for rel in changed:
            live, old = os.path.join(self.live, rel), os.path.join(self.undo_dir, rel)
            if os.path.isfile(old):
                shutil.copy2(old, live + ".deploy-new")
                os.replace(live + ".deploy-new", live)
            elif os.path.isfile(live):
                os.remove(live)  # it was new

    def install_units(self, units):
        """Unit files from the repo that differ from the installed ones (installed in `units`, a list the caller
        keeps so undo_units can put the old ones back). Returns [(name, was_new)]."""
        src = os.path.join(self.live, UNIT_SRC)  # already copied in
        out = []
        if not os.path.isdir(src):
            return out
        os.makedirs(self.unit_dir, exist_ok=True)
        for name in sorted(os.listdir(src)):
            dst = os.path.join(self.unit_dir, name)
            if not os.path.isfile(dst) or not same(os.path.join(src, name), dst):
                out.append((name, not os.path.isfile(dst)))
                units.append(name)
                if os.path.isfile(dst):
                    os.makedirs(os.path.join(self.undo_dir, "units"), exist_ok=True)
                    shutil.copy2(dst, os.path.join(self.undo_dir, "units", name))
                shutil.copy2(os.path.join(src, name), dst)
        if out:
            self.systemctl("daemon-reload")
            for name, new in out:
                if name.endswith(".timer"):
                    self.systemctl("enable", "--now", name) if new else self.systemctl("restart", name)
                elif new and name == "nuauto-web.service":
                    self.systemctl("enable", "--now", name)
        return out

    def undo_units(self, units):
        for name in units:
            dst, old = os.path.join(self.unit_dir, name), os.path.join(self.undo_dir, "units", name)
            if os.path.isfile(old):
                shutil.copy2(old, dst)
            elif os.path.isfile(dst):
                os.remove(dst)

    def restart_web(self):
        self.systemctl("restart", "nuauto-web.service")
        time.sleep(3)
        state = self.systemctl("is-active", "nuauto-web.service").stdout.strip()
        if state != "active":
            raise DeployError(f"nuauto-web is {state or 'not running'} after the restart")

    # ---- one run
    def run(self):
        """0 = nothing to do or deployed (or GitHub not reachable: tried again next time); 1 = failed, the old code
        is still in place."""
        state = self.load_state()
        try:
            wanted = self.remote_sha()
            if state.get("sha") == wanted:
                return 0
            if self.update_running():
                self.say(f"update is running; will deploy {wanted[:7]} on the next run")
                return 0
            sha = self.fetch()
        except (DeployError, OSError, subprocess.SubprocessError) as e:
            self.say(f"cannot get main from GitHub: {e}")
            return 0
        changed, units, restarted = [], [], False
        try:
            subject = self.git("log", "-1", "--format=%s", sha, cwd=self.repo)
            self.unpack(sha)
            self.check_imports()
            changed = self.changed_files()
            self.copy_in(changed)
            if "pyproject.toml" in changed:
                self.install_deps()
            installed = self.install_units(units)
            web = bool(set(changed) & set(WEB_CODE)) or "pyproject.toml" in changed or \
                any(n == "nuauto-web.service" for n, _ in installed)
            if web:
                restarted = True
                self.restart_web()
        except Exception as e:  # any failure: back to the old files and units, then say so (once per commit)
            self.undo(changed)
            self.undo_units(units)
            try:
                if units:
                    self.systemctl("daemon-reload")
                if restarted:
                    self.systemctl("restart", "nuauto-web.service")
            except Exception:
                pass
            self.say(f"deploy of {sha[:7]} FAILED: {e}")
            if state.get("failed") != sha:
                try:
                    self.announce(f"**Deploy failed** ({sha[:7]}): {str(e)[:300]}\n"
                                  f"The homelab keeps running {str(state.get('sha') or 'its old code')[:7]}.")
                except Exception:
                    pass
            self.save_state({**state, "failed": sha, "error": str(e)[:300]})
            return 1
        self.save_state({"sha": sha, "subject": subject[:100]})
        what = (f"{len(changed)} file{'s' if len(changed) != 1 else ''}" + (", web restarted" if web else "")
                + (f", units: {', '.join(n for n, _ in installed)}" if installed else "")
                + (", package reinstalled" if "pyproject.toml" in changed else ""))
        self.say(f"deployed {sha[:7]} ({subject[:60]}): {what}" if changed else f"deployed {sha[:7]}: nothing to copy")
        if changed:
            try:
                self.announce(f"**Deployed** {sha[:7]}: {subject[:80]}\n{what}")
            except Exception:
                pass
        return 0


def main(argv=None):
    if not config.IS_SERVER:
        sys.exit("nuauto deploy runs on the homelab (nuauto-deploy.timer). On the laptop it starts that run.")
    if not config.DEPLOY_FROM_GIT:
        sys.exit('Set "deploy_from_git": true in local_config.json first (docs/DEPLOY.md).')
    return Deploy().run()


if __name__ == "__main__":
    sys.exit(main())

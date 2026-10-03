"""Offline checks that sync.CODE (the files pushed to the homelab) is complete. Run: python test_sync.py

Nothing is imported or executed: modules are read with ast. A module imported by pushed code but missing
from CODE would crash the homelab's twice-daily update (ImportError), so this guards the push list.
"""
import ast
import fnmatch
import glob
import os
import re

import config
import sync

ROOT = config.PROJECT_DIR
NOT_PUSHED = ["test_*.py", "oauth_test.py", "setup_sheet.py"]  # tests and one-off setup scripts stay on the laptop
SCRIPT = "nuworks"  # Python script without the .py extension


def pushed_exempt(name):
    return any(fnmatch.fnmatch(name, pat) for pat in NOT_PUSHED)


def local_modules():
    """Top-level project .py files that must reach the homelab."""
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py")))
    return [n for n in names if not pushed_exempt(n)]


def parse(name):
    with open(os.path.join(ROOT, name)) as f:
        return ast.parse(f.read(), filename=name)


def imported_local_files(name):
    """Local .py files that `name` imports (anywhere in the file, including inside functions)."""
    found = set()
    for node in ast.walk(parse(name)):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module.split(".")[0]]
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            raise AssertionError(f"{name}: relative import (use plain top-level imports)")
        for m in mods:
            if os.path.exists(os.path.join(ROOT, m + ".py")):
                found.add(m + ".py")
    return found


def string_constants(name):
    return {n.value for n in ast.walk(parse(name)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def main():
    code = sync.CODE
    code_py = [c for c in code if c.endswith(".py") or c == SCRIPT]

    # CODE itself: no duplicates, relative paths inside the project dir (pushed with rsync -R), every file exists
    assert len(code) == len(set(code)), f"duplicates in CODE: {sorted(c for c in set(code) if code.count(c) > 1)}"
    for c in code:
        assert not os.path.isabs(c) and ".." not in c.split("/"), f"CODE entry {c!r} must be a path inside the project dir"
        assert os.path.isfile(os.path.join(ROOT, c)), f"CODE lists {c!r} but it does not exist"
    assert SCRIPT in code and "sync.py" in code and "config.py" in code
    for c in code_py:
        parse(c)  # every pushed Python file at least parses

    # tests and one-offs are never pushed
    assert not [c for c in code if pushed_exempt(c)], [c for c in code if pushed_exempt(c)]

    # every local module imported by pushed code (and by the nuworks script) is itself pushed
    for c in code_py:
        missing = imported_local_files(c) - set(code)
        assert not missing, f"{c} imports {sorted(missing)} which are not in sync.CODE"

    # a pushed file must not import a file that is deliberately not pushed either
    for c in code_py:
        bad = [m for m in imported_local_files(c) if pushed_exempt(m)]
        assert not bad, f"{c} imports {bad}, which are never pushed"

    # files started by name (e.g. subprocess "apply.py") are pushed too
    local_py = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))}
    for c in code_py:
        named = {s for s in string_constants(c) if s in local_py} - {c}
        missing = named - set(code)
        assert not missing, f"{c} refers to {sorted(missing)} by name, not in sync.CODE"

    # every *_PROMPT.md referenced in jobs.py / daily.py (or any pushed file) exists and is pushed
    prompt = re.compile(r"\b[A-Z][A-Z_]*_PROMPT\.md\b")
    referenced = set()
    for c in code_py:
        for s in string_constants(c):
            referenced.update(prompt.findall(s))
    assert {"TRIAGE_PROMPT.md", "SCORE_PROMPT.md", "CATEGORY_PROMPT.md"} <= referenced, referenced
    for p in sorted(referenced):
        assert os.path.isfile(os.path.join(ROOT, p)), f"{p} is referenced but missing"
        assert p in code, f"{p} is referenced but not in sync.CODE"
    # and a prompt file on disk that nothing pushes is a forgotten file too
    for p in sorted(glob.glob(os.path.join(ROOT, "*_PROMPT.md"))):
        assert os.path.basename(p) in code, f"{os.path.basename(p)} exists but is not in sync.CODE"

    # reverse: every top-level project module (not a test / one-off) is in CODE, so a new one can't be forgotten
    for m in local_modules():
        assert m in code, f"{m} is a project module but is not in sync.CODE"

    print("All sync checks passed.")


if __name__ == "__main__":
    main()

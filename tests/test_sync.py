"""Offline checks that sync.CODE (the files pushed to the homelab) is complete. Run: python tests/test_sync.py

Nothing is imported or executed from the pushed files: they are read with ast. A module imported by pushed
code but missing from CODE would crash the homelab's twice-daily update (ImportError), so this guards the list.
"""
import ast
import glob
import os
import re

from nuauto import config, sync

ROOT = config.PROJECT_DIR
PKG = "src/nuauto"


def parse(path):
    with open(os.path.join(ROOT, path)) as f:
        return ast.parse(f.read(), filename=path)


def imported_modules(path):
    """nuauto modules a file imports (anywhere, including inside functions), as src/nuauto/<name>.py paths."""
    found = set()
    for node in ast.walk(parse(path)):
        names = []
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            raise AssertionError(f"{path}: relative import (use `from nuauto import x`)")
        if isinstance(node, ast.ImportFrom) and node.module == "nuauto":
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("nuauto."):
            names = [node.module.split(".")[1]]
        elif isinstance(node, ast.Import):
            names = [a.name.split(".")[1] for a in node.names if a.name.startswith("nuauto.")]
        for n in names:
            found.add(f"{PKG}/{n}.py")
    return found


def strings(path):
    return {n.value for n in ast.walk(parse(path)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def main():
    code = sync.CODE
    code_py = [c for c in code if c.endswith(".py")]

    # CODE itself: no duplicates, relative paths inside the project dir (pushed with rsync -R), every file exists
    assert len(code) == len(set(code)), f"duplicates in CODE: {sorted(c for c in set(code) if code.count(c) > 1)}"
    for c in code:
        assert not os.path.isabs(c) and ".." not in c.split("/"), f"CODE entry {c!r} must be a path inside the project dir"
        assert os.path.isfile(os.path.join(ROOT, c)), f"CODE lists {c!r} but it does not exist"
    for must in ("pyproject.toml", f"{PKG}/__init__.py", f"{PKG}/cli.py", f"{PKG}/sync.py", f"{PKG}/config.py"):
        assert must in code, f"{must} is not in sync.CODE"
    for c in code_py:
        parse(c)  # every pushed Python file at least parses

    # tests are never pushed; no Python code is left outside the package
    assert not [c for c in code if c.startswith("tests/")], "tests are not pushed"
    assert not glob.glob(os.path.join(ROOT, "*.py")), "Python code belongs in src/nuauto/ (or tests/)"

    # every package module is pushed, and everything pushed code imports is pushed too
    for m in sorted(glob.glob(os.path.join(ROOT, PKG, "*.py"))):
        rel = os.path.relpath(m, ROOT)
        assert rel in code, f"{rel} is a package module but is not in sync.CODE"
    for c in code_py:
        missing = imported_modules(c) - set(code)
        assert not missing, f"{c} imports {sorted(missing)} which are not in sync.CODE"

    # every *_PROMPT.md named in pushed code exists in prompts/ and is pushed; no prompt is left unpushed
    prompt = re.compile(r"\b[A-Z][A-Z_]*_PROMPT\.md\b")
    referenced = set()
    for c in code_py:
        for s in strings(c):
            referenced.update(prompt.findall(s))
    assert {"TRIAGE_PROMPT.md", "SCORE_PROMPT.md", "CATEGORY_PROMPT.md", "ASSIST_PROMPT.md"} <= referenced, referenced
    for p in sorted(referenced):
        assert os.path.isfile(os.path.join(ROOT, "prompts", p)), f"prompts/{p} is referenced but missing"
        assert "prompts/" + p in code, f"prompts/{p} is referenced but not in sync.CODE"
    assert not glob.glob(os.path.join(ROOT, "*_PROMPT.md")), "prompt files belong in prompts/"
    for p in sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md"))):
        assert "prompts/" + os.path.basename(p) in code, f"prompts/{os.path.basename(p)} exists but is not in sync.CODE"

    # the homelab's units run the installed command, never a file path that no longer exists
    for unit in glob.glob(os.path.join(ROOT, "deploy", "systemd", "*.service")):
        for line in open(unit):
            if line.startswith("ExecStart="):
                assert "/.venv/bin/nuauto " in line and ".py" not in line, f"{os.path.basename(unit)}: {line.strip()}"

    print("All sync checks passed.")


if __name__ == "__main__":
    main()

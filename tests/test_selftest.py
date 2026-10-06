"""`nuauto selftest` from a source install (the packaged app runs the same code). Run: python test_selftest.py"""
import os
import subprocess
import sys

env = {k: v for k, v in os.environ.items() if k not in ("NUAUTO_STATE_DIR", "NUAUTO_DEMO")}
r = subprocess.run([sys.executable, "-m", "nuauto", "selftest"], capture_output=True, text=True, timeout=600, env=env)
assert r.returncode == 0 and "Self-test passed." in r.stdout, (r.stdout[-1500:], r.stderr[-1500:])
assert "Review: approved" in r.stdout and "question came up" in r.stdout and "Sheet:" in r.stdout, r.stdout
print("Self-test check passed.")

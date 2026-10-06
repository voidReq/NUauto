# PyInstaller spec for the packaged NUauto (packaging/build.sh runs it). One folder with the app's own Python and
# libraries; on macOS wrapped in NUauto.app. Inside the bundle: the code, prompts/, the page (gui_static/), the GTK
# window helper (run by the system's python3 on Linux) and local_config.example.json. Your files are never in it.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import nuauto  # noqa: E402

datas = [
    (os.path.join(ROOT, "prompts"), "prompts"),
    (os.path.join(ROOT, "local_config.example.json"), "."),
    (os.path.join(ROOT, "src", "nuauto", "gui_static"), os.path.join("nuauto", "gui_static")),
    (os.path.join(ROOT, "src", "nuauto", "window_gtk.py"), "nuauto"),
]
# nuauto's modules are imported inside functions and by name (cli's tools): collect them all
hidden = [m for m in collect_submodules("nuauto") if m != "nuauto.window_gtk"]

a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[os.path.join(ROOT, "src")], datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "_tkinter", "pytest", "IPython", "matplotlib", "pandas",
                                             "nuauto.window_gtk", "gi"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NUauto", console=sys.platform != "darwin")
coll = COLLECT(exe, a.binaries, a.datas, name="NUauto")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="NUauto.app", icon=os.path.join(SPECPATH, "build", "NUauto.icns"),
                 bundle_identifier="com.nuauto.gui",
                 info_plist={"CFBundleName": "NUauto", "CFBundleDisplayName": "NUauto",
                             "CFBundleShortVersionString": nuauto.__version__, "CFBundleVersion": nuauto.__version__,
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})

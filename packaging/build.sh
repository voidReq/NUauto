#!/bin/sh
# Build the packaged NUauto for this machine (CI does this for every release: .github/workflows/release.yml).
#   Linux  dist/NUauto-<version>-linux-<arch>.AppImage   one file: make it executable, double-click it
#   macOS  dist/NUauto-<version>-macos-<arch>.dmg        NUauto.app inside: drag it to Applications
# Needs uv; on Linux also curl (to fetch appimagetool) and binutils (PyInstaller uses objdump).
# Then check it: packaging/smoke.py (see its docstring).
set -eu
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BUILD="$ROOT/packaging/build"
OUT="$ROOT/dist"
rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD" "$OUT"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
"$UV" venv --python 3.12 "$BUILD/venv"
PY="$BUILD/venv/bin/python"
if [ "$(uname -s)" = Darwin ]; then
  # wheels that run on macOS 12 and newer, not only on this (newer) build machine
  export MACOSX_DEPLOYMENT_TARGET=12.0
  case "$(uname -m)" in arm64) TARGET=aarch64-apple-darwin ;; *) TARGET=x86_64-apple-darwin ;; esac
  "$UV" pip install --python "$PY" --python-platform "$TARGET" "$ROOT" "pyinstaller==6.22.3"
else
  "$UV" pip install --python "$PY" "$ROOT" "pyinstaller==6.22.3"
fi
VERSION="$("$PY" -c 'import nuauto; print(nuauto.__version__)')"
ARCH="$(uname -m)"

if [ "$(uname -s)" = Darwin ]; then  # the app icon, from the 1024 px PNG
  ICONSET="$BUILD/NUauto.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" packaging/icon-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z "$d" "$d" packaging/icon-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$BUILD/NUauto.icns"
fi

"$BUILD/venv/bin/pyinstaller" --noconfirm --clean --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/nuauto.spec

if [ "$(uname -s)" = Darwin ]; then
  [ "$ARCH" = arm64 ] || ARCH=x86_64
  STAGE="$BUILD/dmg"
  mkdir -p "$STAGE"
  cp -R "$BUILD/dist/NUauto.app" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname NUauto -srcfolder "$STAGE" -ov -format UDZO "$OUT/NUauto-$VERSION-macos-$ARCH.dmg"
else
  APPDIR="$BUILD/NUauto.AppDir"
  mkdir -p "$APPDIR/usr/lib"
  cp -R "$BUILD/dist/NUauto" "$APPDIR/usr/lib/nuauto"
  cp packaging/AppRun "$APPDIR/AppRun"
  cp packaging/nuauto.desktop "$APPDIR/nuauto.desktop"
  cp packaging/icon-256.png "$APPDIR/nuauto.png"
  ln -s nuauto.png "$APPDIR/.DirIcon"
  TOOL="$BUILD/appimagetool"
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
  export ARCH  # appimagetool reads the target architecture from it
  APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT/NUauto-$VERSION-linux-$ARCH.AppImage"
fi
ls -la "$OUT"

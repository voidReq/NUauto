#!/bin/sh
# Run a built AppImage on several Linux distributions in containers (podman or docker), the way a user's machine
# would: the libraries Firefox needs, then the app's own checks: `_window`, `doctor --json` and `selftest` (demo mode
# end to end in a hidden browser). Nothing of yours is touched: each run is a fresh container.
#
#   sh packaging/distros.sh dist/NUauto-<version>-linux-x86_64.AppImage [distro ...]
#   distros: ubuntu-22.04 debian-12 ubuntu-24.04 fedora arch (default: all). Logs and a summary in packaging/build/distros/
#
# Build the AppImage on Ubuntu 22.04 (CI does; locally: packaging/build.sh inside an ubuntu:22.04 container) so it
# runs on the older distributions too. Needs network (images, packages, Playwright's Firefox per distribution).
set -u
APPIMAGE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
shift
[ -f "$APPIMAGE" ] || { echo "No AppImage at $APPIMAGE"; exit 1; }
ENGINE="$(command -v podman || command -v docker)" || { echo "Needs podman or docker"; exit 1; }
OUT="$(cd "$(dirname "$0")" && pwd)/build/distros"
mkdir -p "$OUT"
: > "$OUT/summary.txt"
DISTROS="${*:-ubuntu-22.04 debian-12 ubuntu-24.04 fedora arch}"

# shellcheck disable=SC2016  # these run inside the container: its shell expands them
CHECKS='set -e
export APPIMAGE_EXTRACT_AND_RUN=1
APP=/app/NUauto.AppImage
. /etc/os-release
echo "== $PRETTY_NAME, $(ldd --version 2>&1 | head -1)"
echo "window: $($APP _window)"
$APP doctor --json | head -c 160; echo
$APP selftest'
# shellcheck disable=SC2016
APT='export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null
APPIMAGE_EXTRACT_AND_RUN=1 /app/NUauto.AppImage _playwright install-deps firefox >/dev/null 2>&1'

status=0
for d in $DISTROS; do
  case "$d" in
    ubuntu-22.04) image=docker.io/library/ubuntu:22.04 deps="$APT" ;;
    ubuntu-24.04) image=docker.io/library/ubuntu:24.04 deps="$APT" ;;
    debian-12) image=docker.io/library/debian:12 deps="$APT" ;;
    fedora) image=docker.io/library/fedora:latest deps='dnf install -y -q firefox >/dev/null 2>&1' ;;
    arch) image=docker.io/library/archlinux:latest deps='pacman -Sy --noconfirm --needed firefox >/dev/null 2>&1' ;;
    *) echo "unknown distro $d"; status=1; continue ;;
  esac
  start=$(date +%s)
  if "$ENGINE" run --rm --security-opt label=disable -v "$APPIMAGE:/app/NUauto.AppImage:ro" "$image" \
      bash -c "$deps
$CHECKS" > "$OUT/$d.log" 2>&1; then
    line="PASS $d ($(( $(date +%s) - start )) s)"
  else
    line="FAIL $d ($(( $(date +%s) - start )) s): $(tail -3 "$OUT/$d.log" | tr '\n' ' ' | cut -c1-300)"
    status=1
  fi
  echo "$line" | tee -a "$OUT/summary.txt"
done
exit $status

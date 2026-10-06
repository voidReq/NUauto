#!/bin/sh
# NUauto installer for Linux and macOS. Everything goes in your home folder; nothing needs sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/voidReq/NUauto/main/install.sh | sh
#   ./install.sh                 (from a checkout: installs that checkout)
#   NUAUTO_DIR=~/somewhere sh install.sh
#
# It installs uv (which brings its own Python 3.12, so your system's Python version doesn't matter), the NUauto
# code (git) with its Python packages, Playwright's Firefox, the `nuauto` command in ~/.local/bin and an app icon,
# then opens NUauto: its setup screen walks you through the rest (Google, your sheet, NUworks, Claude Code).
# Run it again any time to update.
set -eu

REPO="https://github.com/voidReq/NUauto.git"
DIR="${NUAUTO_DIR:-$HOME/NUauto}"

say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

case "$(uname -s)" in
  Linux | Darwin) ;;
  *) echo "NUauto runs on Linux and macOS."; exit 1 ;;
esac

if ! have git; then
  if [ "$(uname -s)" = Darwin ]; then
    say "git is missing. macOS will offer to install its command line tools (they include git)."
    xcode-select --install 2>/dev/null || true
    echo "When that finishes, run this installer again."
  else
    echo "git is missing. Install it with your package manager (sudo apt install git / sudo dnf install git /"
    echo "sudo pacman -S git), then run this installer again."
  fi
  exit 1
fi
if ! have curl; then
  echo "curl is missing. Install it with your package manager, then run this installer again."
  exit 1
fi

# the code: this checkout, an earlier install (updated), or a fresh clone
if [ -f ./pyproject.toml ] && grep -q '^name = "nuauto"' ./pyproject.toml; then
  DIR="$(pwd)"
  say "Installing this checkout ($DIR)"
elif [ -d "$DIR/.git" ]; then
  say "Updating $DIR"
  git -C "$DIR" pull --ff-only
else
  say "Downloading NUauto into $DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

# uv: a Python manager; it downloads Python 3.12 for NUauto if your system doesn't have it
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  say "Installing uv (Python manager, https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

say "Installing NUauto's Python packages"
[ -x .venv/bin/python ] || "$UV" venv --python 3.12 .venv
# never compile cryptography (Intel Macs: uv takes the newest ready-made one instead of needing Rust and OpenSSL)
"$UV" pip install --python .venv/bin/python --only-binary cryptography -e .

say "Installing Playwright's Firefox (the browser NUauto drives)"
.venv/bin/python -m playwright install firefox
if ! .venv/bin/python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p: p.firefox.launch(headless=True).close()" 2>/dev/null; then
  echo
  echo "Firefox is installed but does not start: it needs some system libraries."
  if have apt-get; then
    echo "Run:  sudo $DIR/.venv/bin/python -m playwright install-deps firefox"
  else
    echo "Install the libraries Playwright's Firefox needs with your package manager"
    echo "(https://playwright.dev/python/docs/browsers), then run this installer again."
  fi
fi

say "Adding the nuauto command to ~/.local/bin"
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/.venv/bin/nuauto" "$HOME/.local/bin/nuauto"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    case "$(basename "${SHELL:-sh}")" in
      zsh) RC="$HOME/.zshrc" ;;
      bash) if [ "$(uname -s)" = Darwin ]; then RC="$HOME/.bash_profile"; else RC="$HOME/.bashrc"; fi ;;
      *) RC="$HOME/.profile" ;;
    esac
    if ! grep -qs 'NUauto: ~/.local/bin' "$RC"; then
      # shellcheck disable=SC2016  # $HOME and $PATH go into the file as written, for the shell to expand
      printf '\n# NUauto: ~/.local/bin holds the nuauto command\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
      echo "Added ~/.local/bin to your PATH in $RC (new terminals pick it up)."
    fi
    ;;
esac

say "Adding NUauto to your apps"
.venv/bin/python -m nuauto.onboard launcher

say "Done. Opening NUauto: its setup screen walks you through the rest."
nohup .venv/bin/nuauto gui >/dev/null 2>&1 &
echo "(Later: open NUauto from your apps, or run: nuauto gui)"

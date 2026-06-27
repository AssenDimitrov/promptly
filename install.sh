#!/usr/bin/env bash
# Promptly installer.
#
#   curl -fsSL https://raw.githubusercontent.com/AssenDimitrov/promptly/main/install.sh | bash
#
# Installs into ~/.local (override with PROMPTLY_PREFIX), in a private virtualenv
# so PySide6 never touches your system Python, and puts `promptly` on PATH.
# Re-run any time to update. Uninstall:  rm -rf ~/.local/share/promptly ~/.local/bin/promptly
set -euo pipefail

REPO="${PROMPTLY_REPO:-https://raw.githubusercontent.com/AssenDimitrov/promptly/main}"
PREFIX="${PROMPTLY_PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/promptly"
BIN="$PREFIX/bin"

say() { printf '\033[1;32m▸\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "python3 is required but not found."
command -v curl    >/dev/null 2>&1 || die "curl is required but not found."

say "Installing Promptly into $SHARE"
mkdir -p "$SHARE" "$BIN"

for f in promptly.py register-claude-window.sh; do
  say "fetching $f"
  curl -fsSL "$REPO/$f" -o "$SHARE/$f" || die "could not download $f from $REPO"
done
chmod +x "$SHARE/register-claude-window.sh"

if command -v uv >/dev/null 2>&1; then
  say "creating a private virtualenv with uv (PySide6 is a large download, ~100MB)"
  uv venv "$SHARE/.venv"
  say "installing PySide6 — live progress below"
  VIRTUAL_ENV="$SHARE/.venv" uv pip install PySide6-Essentials
else
  say "creating a private virtualenv (PySide6 is a large download, ~100MB)"
  python3 -m venv "$SHARE/.venv"
  "$SHARE/.venv/bin/python" -m pip install --quiet --upgrade pip
  say "installing PySide6 — live progress below"
  "$SHARE/.venv/bin/python" -m pip install --progress-bar on PySide6-Essentials
fi

# `promptly` launcher → runs the bundled script with the private venv's Python
cat > "$BIN/promptly" <<EOF
#!/usr/bin/env bash
exec "$SHARE/.venv/bin/python" "$SHARE/promptly.py" "\$@"
EOF
chmod +x "$BIN/promptly"

say "installed:  promptly  →  $BIN"

case ":$PATH:" in
  *":$BIN:"*)
    say "$BIN is already on your PATH — you're all set."
    ;;
  *)
    # Build a portable export line ($HOME-relative if BIN lives under $HOME).
    PATH_DIR="$BIN"
    case "$BIN" in "$HOME"/*) PATH_DIR="\$HOME${BIN#"$HOME"}" ;; esac
    EXPORT_LINE="export PATH=\"$PATH_DIR:\$PATH\""

    # Pick the right startup file for the user's shell.
    case "$(basename "${SHELL:-}")" in
      zsh)  RC="$HOME/.zshrc" ;;
      bash) [ "$(uname)" = "Darwin" ] && RC="$HOME/.bash_profile" || RC="$HOME/.bashrc" ;;
      fish) RC="$HOME/.config/fish/config.fish" ;;
      *)    RC="" ;;
    esac

    printf '\n\033[1;33m!  %s is not on your PATH yet. To fix it:\033[0m\n' "$BIN"
    if [ "$(basename "${SHELL:-}")" = "fish" ]; then
      printf '\n    fish_add_path %s\n\n' "$BIN"
    elif [ -n "$RC" ]; then
      printf '\n    echo '"'"'%s'"'"' >> %s\n    source %s\n\n' "$EXPORT_LINE" "$RC" "$RC"
    else
      printf '\n  Add this line to your shell startup file, then restart your shell:\n\n    %s\n\n' "$EXPORT_LINE"
    fi
    ;;
esac

say "try it:  promptly --skin spinner   (then)   curl -s localhost:7654/yellow"

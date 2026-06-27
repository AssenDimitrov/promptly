#!/usr/bin/env bash
# Promptly installer.
#
#   curl -fsSL https://raw.githubusercontent.com/AssenDimitrov/promptly/main/install.sh | bash
#
# Installs into ~/.local (override with PROMPTLY_PREFIX), in a private virtualenv
# so PySide6 never touches your system Python, and puts `promptly` + `tl` on PATH.
# Re-run any time to update. Uninstall:  rm -rf ~/.local/share/promptly ~/.local/bin/{promptly,tl}
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

for f in promptly.py tl register-claude-window.sh; do
  say "fetching $f"
  curl -fsSL "$REPO/$f" -o "$SHARE/$f" || die "could not download $f from $REPO"
done
chmod +x "$SHARE/tl" "$SHARE/register-claude-window.sh"

say "creating a private virtualenv + installing PySide6 (this can take a minute)"
python3 -m venv "$SHARE/.venv"
"$SHARE/.venv/bin/python" -m pip install --quiet --upgrade pip
"$SHARE/.venv/bin/python" -m pip install --quiet PySide6

# `promptly` launcher → runs the bundled script with the private venv's Python
cat > "$BIN/promptly" <<EOF
#!/usr/bin/env bash
exec "$SHARE/.venv/bin/python" "$SHARE/promptly.py" "\$@"
EOF
chmod +x "$BIN/promptly"

# `tl` helper on PATH
ln -sf "$SHARE/tl" "$BIN/tl"

say "installed:  promptly  and  tl  →  $BIN"
case ":$PATH:" in
  *":$BIN:"*) : ;;
  *) printf '\033[1;33m!  Add %s to your PATH, e.g.:\033[0m\n   echo '"'"'export PATH="%s:$PATH"'"'"' >> ~/.zshrc\n' "$BIN" "$BIN" ;;
esac
say "try it:  promptly --skin spinner   (then in another shell)   tl yellow --flash"

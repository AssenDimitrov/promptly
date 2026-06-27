#!/usr/bin/env bash
# Tell the traffic light which window to raise when you LEFT-CLICK it.
# Run automatically by Claude Code's SessionStart hook (see claude-code-settings.json).
# You can also run it by hand from the terminal where Claude Code lives.
#
# At session start, the terminal running Claude is the frontmost window, so on
# macOS we capture *that exact window* (Terminal.app / iTerm2) and store a command
# that re-raises precisely it on click — handy when several terminal windows are
# open. If we can't get a window id, we fall back to focusing the app by name.
set -u
PORT="${TL_PORT:-7654}"

focus() {  # focus <key> <value>   (key = app | title | cmd)
  curl -s -G "localhost:${PORT}/focus" --data-urlencode "$1=$2" >/dev/null 2>&1 || true
}

case "$(uname)" in
  Darwin)
    case "${TERM_PROGRAM:-}" in
      Apple_Terminal) APP="Terminal" ;;
      iTerm.app)      APP="iTerm" ;;
      vscode)         APP="Visual Studio Code" ;;
      WezTerm)        APP="WezTerm" ;;
      Hyper)          APP="Hyper" ;;
      ghostty)        APP="Ghostty" ;;
      *)              APP="${TERM_PROGRAM:-}" ;;
    esac

    # Try to grab the id of the window Claude is in (frontmost right now).
    WIN_ID=""
    case "$APP" in
      Terminal) WIN_ID=$(osascript -e 'tell application "Terminal" to id of front window' 2>/dev/null) ;;
      iTerm)    WIN_ID=$(osascript -e 'tell application "iTerm" to id of current window' 2>/dev/null) ;;
    esac

    if [ "$APP" = "Terminal" ] && [ -n "$WIN_ID" ]; then
      # Raise exactly this Terminal window.
      focus cmd "osascript -e 'tell application \"Terminal\" to activate' -e 'tell application \"Terminal\" to set index of window id ${WIN_ID} to 1'"
    elif [ "$APP" = "iTerm" ] && [ -n "$WIN_ID" ]; then
      # Raise exactly this iTerm window.
      focus cmd "osascript -e 'tell application \"iTerm\"' -e 'activate' -e 'repeat with w in windows' -e 'if id of w is ${WIN_ID} then select w' -e 'end repeat' -e 'end tell'"
    elif [ -n "$APP" ]; then
      # Fallback: focus the terminal app by name (reliable, but app-level only).
      focus app "$APP"
    fi
    ;;

  Linux)
    # X11 only: focus a window by matching part of its title (needs wmctrl or xdotool).
    # If your terminal doesn't set a useful title, give it one in your shell rc:
    #     printf '\033]0;claude-code\007'
    # then export CLAUDE_WINDOW_TITLE=claude-code so this matches it exactly.
    # NOTE: Wayland usually blocks one app from focusing another's window.
    TITLE="${CLAUDE_WINDOW_TITLE:-${TERM_PROGRAM:-Terminal}}"
    focus title "$TITLE"
    ;;
esac

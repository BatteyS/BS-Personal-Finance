#!/bin/sh
# Opens the page: starts it in the background if it is not already running, waits until it
# answers, then opens it in your browser. Personal Finance on the Desktop runs this (install.sh
# makes it), and it works from a terminal too:
#
#     sh launch.sh           # start the page if need be, then open it
#     sh launch.sh --stop    # stop the page this copy started
#
# With phone access on (phone.py), a LaunchAgent runs `launch.sh --serve-always 8501` instead,
# which keeps the page up with no idle stop; the Desktop app then just opens it.
#
# The page runs with no Terminal window. Streamlit can't tell when its last tab closes, so the
# launcher watches the page's open connections (each tab holds one) and stops the page once it
# has had none for twenty minutes. It takes the first free port from 8501 up, and a page this copy
# already started is opened rather than started twice. What it prints goes to
# ~/Library/Logs/Personal Finance.log. When it can't start, this exits 1 and says why on stderr,
# which Personal Finance on the Desktop shows in a dialog.

cd "$(dirname "$0")" || exit 1
ROOT=$(pwd -P)
LOG="$HOME/Library/Logs/Personal Finance.log"
PIDFILE="$ROOT/.page.pid"   # "<pid> <port>" of the page this copy started; gitignored
IDLE_MINUTES=${PF_IDLE_MINUTES:-20}
FIRST_PORT=${PF_PORT:-8501}
LAST_PORT=$((FIRST_PORT + 9))

# Opened from Finder, PATH is only /usr/bin:/bin:/usr/sbin:/sbin. Add what /etc/paths.d names,
# then the places uv installs itself.
if [ -x /usr/libexec/path_helper ]; then
  eval "$(/usr/libexec/path_helper -s)"
fi
PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH
[ -n "${LANG:-}" ] || LANG=en_US.UTF-8   # Finder sets no locale; Python and git expect UTF-8
export LANG

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

notify() {
  osascript -e "display notification \"$1\" with title \"Personal Finance\"" >/dev/null 2>&1 || :
}

mkdir -p "$(dirname "$LOG")" 2>/dev/null
# Keep the log from growing forever: past 1 MB, keep its last 200 KB.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1000000 ]; then
  tail -c 200000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

UV=
for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
         /opt/homebrew/bin/uv /usr/local/bin/uv; do
  if [ -n "$c" ] && [ -x "$c" ]; then UV=$c; break; fi
done
[ -n "$UV" ] || fail "Personal Finance needs uv, which is not installed. Open Terminal and run:

sh \"$ROOT/install.sh\"

It installs what is missing."

# Whether something answers on port $1 ("up") or nothing does ("free").
probe() {
  curl -s -o /dev/null -m "${2:-2}" "http://127.0.0.1:$1/_stcore/health"
  [ $? -eq 7 ] && echo free || echo up
}

# The port of the page this copy started, if it is still running and answering.
our_port() {
  [ -f "$PIDFILE" ] || return 1
  read -r pid port <"$PIDFILE" 2>/dev/null || return 1
  kill -0 "$pid" 2>/dev/null && [ "$(probe "$port")" = up ] && echo "$port"
}

open_page() {
  if [ -n "${PF_NO_BROWSER:-}" ]; then echo "http://localhost:$1/"; return; fi   # for tests
  open "http://localhost:$1/" 2>/dev/null ||
    fail "The page is running, but no browser opened. Open http://localhost:$1/ in yours."
}

# How many connections the page on port $1 has open: one per open tab.
connections() {
  lsof -nP -a -p "$2" -iTCP:"$1" -sTCP:ESTABLISHED 2>/dev/null | awk 'NR > 1' | wc -l | tr -d ' '
}

# The phone's home-screen icon, served from Streamlit's static folder (gitignored).
draw_icon() {
  [ -f static/apple-touch-icon.png ] || "$UV" run --quiet icon.py static/apple-touch-icon.png || :
}

# --serve-always PORT: what phone access's LaunchAgent runs. Becomes the page, in this same
# process, so launchd can see it stop and start it again; no idle stop.
if [ "${1:-}" = --serve-always ]; then
  port=$2
  draw_icon
  echo "$$ $port" >"$PIDFILE"
  exec "$UV" run streamlit run dashboard.py --server.port "$port" --server.address localhost \
    --server.headless true
fi

# --serve PORT: what start_on runs in the background. Runs the page and watches it: once it has
# had no connections for IDLE_MINUTES, it stops it.
if [ "${1:-}" = --serve ]; then
  port=$2
  draw_icon
  "$UV" run streamlit run dashboard.py --server.port "$port" --server.address localhost \
    --server.headless true &
  pid=$!
  echo "$pid $port" >"$PIDFILE"
  idle=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 60
    # A restart's old watcher, from before an update, may have removed this page's record.
    [ -f "$PIDFILE" ] || echo "$pid $port" >"$PIDFILE"
    if [ "$(connections "$port" "$pid")" -gt 0 ]; then idle=0; else idle=$((idle + 1)); fi
    if [ "$idle" -ge "$IDLE_MINUTES" ]; then
      date "+--- %Y-%m-%d %H:%M:%S no open tabs for $IDLE_MINUTES minutes: stopping" >>"$LOG"
      kill "$pid" 2>/dev/null
      break
    fi
  done
  wait "$pid" 2>/dev/null
  # Remove the record only while it's still this page's: a page started since keeps its own.
  if [ "$(cut -d' ' -f1 "$PIDFILE" 2>/dev/null)" = "$pid" ]; then rm -f "$PIDFILE"; fi
  exit 0
fi

if [ "${1:-}" = --stop ]; then
  if launchctl print "gui/$(id -u)/io.github.batteys.bs-personal-finance.page" >/dev/null 2>&1; then
    echo "Phone access keeps the page running. To stop it, turn phone access off: uv run phone.py disable"
    exit 0
  fi
  if [ -f "$PIDFILE" ] && read -r pid port <"$PIDFILE" && kill "$pid" 2>/dev/null; then
    echo "Stopped the page on port $port."
  else
    echo "This copy's page isn't running."
  fi
  rm -f "$PIDFILE"
  exit 0
fi

# Start the page on port $1 in the background and wait until it answers.
start_on() {
  port=$1
  date "+--- %Y-%m-%d %H:%M:%S starting on port $port" >>"$LOG"
  nohup /bin/sh "$ROOT/launch.sh" --serve "$port" >>"$LOG" 2>&1 </dev/null &
  waited=0
  while [ "$(probe "$port" 1)" != up ]; do
    sleep 0.5
    waited=$((waited + 1))
    [ "$waited" -eq 8 ] && notify "Getting the page ready. The first start, or one after an update, can take a minute."
    if [ "$waited" -ge 600 ]; then
      fail "The page hasn't answered after five minutes. What it printed is in $LOG"
    fi
    if [ "$waited" -ge 6 ] && [ ! -f "$PIDFILE" ]; then
      fail "The page didn't start. The last of what it printed:

$(tail -n 12 "$LOG")

All of it is in $LOG"
    fi
  done
}

if port=$(our_port); then
  open_page "$port"
  exit 0
fi
rm -f "$PIDFILE"
port=$FIRST_PORT
while [ "$port" -le "$LAST_PORT" ]; do
  if [ "$(probe "$port")" = free ]; then
    start_on "$port"
    open_page "$port"
    exit 0
  fi
  port=$((port + 1))
done
fail "Ports $FIRST_PORT to $LAST_PORT are all taken by other programs, so the page has nowhere to run. Quit one of them, then open Personal Finance again."

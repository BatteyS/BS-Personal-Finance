#!/bin/sh
# Personal Finance, set up on a Mac in one step. When it finishes, Personal Finance is on your
# Desktop: double-click it to open the page.
#
# Paste this line into Terminal (Applications > Utilities) and press Return:
#
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.sh)"
#
# In a copy you already have, run it from that folder instead: sh install.sh
#
# It installs only what is missing, all of it into your own home folder, and asks for no password
# itself. Apple's Command Line Tools come through a window of Apple's. In order:
#
#   1. Apple's Command Line Tools, which bring git. macOS asks you to click Install.
#   2. uv, which runs the Python this tracker is pinned to.
#   3. Your copy, in ~/personal-finance, and its Python packages. A copy that's already there is
#      brought up to date, unless it has changes of yours.
#   4. Personal Finance on your Desktop, which starts the page and opens it.
#   5. If you want it, Personal Finance on your phone too: an icon on its home screen that opens
#      the page from anywhere over Tailscale (phone.py).
#
# Run it again whenever you like: what is done is skipped, your copy is brought up to the newest
# version, and Personal Finance on the Desktop is made again, which is how to bring it back or point
# it at a copy you moved. Everything it runs
# prints into ~/Library/Logs/Personal Finance install.log.
#
# Options:
#   --dir FOLDER   where the copy goes (default ~/personal-finance). Run from inside a copy,
#                  that copy is used.
#   --no-open      don't open the page at the end
#   --phone        set up Personal Finance on your phone, without asking first
#
# Only macOS for now. On Linux, follow Get started in README.md.

set -u

REPO_URL=${PF_REPO_URL:-https://github.com/BatteyS/BS-Personal-Finance.git}
README_URL="https://github.com/BatteyS/BS-Personal-Finance#get-started"
APP_NAME="Personal Finance"
APP_ID="io.github.batteys.bs-personal-finance"
DESKTOP=${PF_DESKTOP:-$HOME/Desktop}
LOG="$HOME/Library/Logs/Personal Finance install.log"
MIN_MACOS=13

BOLD=$(printf '\033[1m'); PLAIN=$(printf '\033[0m')
[ -t 1 ] || { BOLD=; PLAIN=; }
step() { printf '\n%s%s%s\n' "$BOLD" "$*" "$PLAIN"; }
ok()   { printf '  %sok%s    %s\n' "$BOLD" "$PLAIN" "$*"; }
note() { printf '        %s\n' "$*"; }
quiet() { "$@" >>"$LOG" 2>&1; }
die() {
  printf '\n%sStopped:%s %s\n\nRunning the same command again picks up where this stopped.\n' "$BOLD" "$PLAIN" "$*" >&2
  exit 1
}
show_log_tail() {
  printf '\n  The end of %s:\n' "$LOG"
  tail -n 15 "$LOG" 2>/dev/null | sed 's/^/    /'
}
# Piped in from curl, stdin is the script itself, so waiting for Return reads /dev/tty.
wait_for_return() {
  if [ -r /dev/tty ]; then printf '%s' "$1"; read -r _ </dev/tty; fi
}

DIR=
OPEN=1
PHONE=
UPDATED=
while [ $# -gt 0 ]; do
  case $1 in
    --dir) [ $# -ge 2 ] || die "--dir needs a folder after it."; DIR=$2; shift ;;
    --no-open) OPEN= ;;
    --phone) PHONE=yes ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "$1 isn't an option. The options are --dir FOLDER, --no-open and --phone." ;;
  esac
  shift
done

# A copy of this tracker: a folder with the dashboard and this installer in it.
is_copy() { [ -f "$1/dashboard.py" ] && [ -f "$1/launch.sh" ]; }

# Run from inside a copy (sh install.sh), that copy is used.
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)
if [ -z "$DIR" ] && [ -n "$here" ] && is_copy "$here"; then
  DIR=$here
fi
DIR=${DIR:-$HOME/personal-finance}
case $DIR in /*) ;; *) DIR="$(pwd -P)/$DIR" ;; esac

# ------------------------------------------------------------------ checks

preflight() {
  [ "$(uname -s)" = Darwin ] || die "This installer is for macOS. On Linux, follow Get started in README.md: $README_URL"
  [ "$(id -u)" != 0 ] || die "Run it as yourself, without sudo. Everything goes into your own home folder."
  mkdir -p "$(dirname "$LOG")" 2>/dev/null
  : >"$LOG" 2>/dev/null || LOG=/dev/null
  version=$(sw_vers -productVersion 2>/dev/null)
  major=${version%%.*}
  case $major in ''|*[!0-9]*) major=0 ;; esac
  [ "$major" -ge "$MIN_MACOS" ] || die "This Mac has macOS ${version:-of an unknown version}; Personal Finance needs macOS $MIN_MACOS (Ventura) or newer. A newer macOS, if this Mac can take one, is in System Settings > General > Software Update."
  ok "macOS $version, $(uname -m)"
  date "+--- install.sh, %Y-%m-%d %H:%M:%S, macOS $version, $(uname -m)" >>"$LOG"
  for d in "$HOME/.local" "$HOME/.local/bin"; do
    if [ -e "$d" ] && [ ! -w "$d" ]; then
      die "$d belongs to another user (usually root, from an earlier sudo), so uv can't install into it. Give it back to yourself with: sudo chown -R $(id -un) \"$HOME/.local\""
    fi
  done
}

# -------------------------------------------------------- git (Command Line Tools)

# A git that runs. Without the Command Line Tools, /usr/bin/git is only a stub that opens Apple's
# install window, so ask xcode-select first.
git_ok() { xcode-select -p >/dev/null 2>&1 && git --version >/dev/null 2>&1; }

ensure_git() {
  step "1. git (Apple's Command Line Tools)"
  if git_ok; then
    ok "$(git --version)"
    return
  fi
  note "macOS will open a window asking to install the Command Line Tools: click Install."
  xcode-select --install >>"$LOG" 2>&1
  note "Waiting for them to finish (several minutes)..."
  waited=0
  until git_ok; do
    sleep 10
    waited=$((waited + 10))
    [ "$waited" -ge 3600 ] && die "The Command Line Tools haven't finished after an hour. When the window says they're installed, run this again."
  done
  ok "$(git --version)"
}

# ---------------------------------------------------------------------- uv

find_uv() {
  UV=
  for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
           /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -n "$c" ] && [ -x "$c" ]; then UV=$c; return 0; fi
  done
  return 1
}

ensure_uv() {
  step "2. uv, which runs Python"
  if find_uv; then
    ok "$("$UV" --version 2>/dev/null) at $UV"
    return
  fi
  note "Installing uv from astral.sh..."
  curl -LsSf https://astral.sh/uv/install.sh 2>>"$LOG" | sh >>"$LOG" 2>&1
  find_uv || { show_log_tail; die "uv didn't install: see above."; }
  ok "$("$UV" --version 2>/dev/null) at $UV"
}

# ---------------------------------------------------------------- the copy

# Bring a copy that's already there up to date: a fast-forward of main, and only when it's on main
# with no changes of yours, so nothing of yours is ever written over (updates.py does the same).
update_copy() {
  [ -d "$DIR/.git" ] || return 0
  branch=$(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$branch" != main ]; then
    note "It's on the branch $branch, not main, so it wasn't updated."
    return 0
  fi
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    note "It has changes of yours to its files, so it wasn't updated."
    return 0
  fi
  before=$(git -C "$DIR" rev-parse HEAD)
  if ! GIT_TERMINAL_PROMPT=0 quiet git -C "$DIR" pull --ff-only --quiet origin main; then
    note "It couldn't be brought up to date just now (see $LOG); it works as it is."
    return 0
  fi
  if [ "$(git -C "$DIR" rev-parse HEAD)" = "$before" ]; then
    ok "already up to date: $(git -C "$DIR" log -1 --format=%s)"
  else
    UPDATED=yes
    ok "brought up to date: $(git -C "$DIR" log -1 --format=%s)"
  fi
}

# A page started before the update still runs the old code: start it again.
restart_page() {
  [ -n "$UPDATED" ] || return 0
  agent="gui/$(id -u)/io.github.batteys.bs-personal-finance.page"
  if launchctl print "$agent" >/dev/null 2>&1; then
    quiet launchctl kickstart -k "$agent"
  else
    quiet /bin/sh "$DIR/launch.sh" --stop
  fi
}

ensure_copy() {
  step "3. Your copy"
  if [ -e "$DIR" ] && ! is_copy "$DIR"; then
    die "$DIR is already there and isn't a copy of Personal Finance. Move it, or name another folder: add --dir ~/some-other-folder to the command."
  fi
  if [ ! -e "$DIR" ]; then
    GIT_TERMINAL_PROMPT=0 quiet git clone --quiet "$REPO_URL" "$DIR" ||
      { show_log_tail; die "Couldn't download Personal Finance from $REPO_URL: see above."; }
    ok "made $DIR"
  else
    ok "$DIR"
    update_copy
  fi
  case $DIR in
    "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Library/Mobile Documents"/*)
      note "This folder may be kept in iCloud Drive, which can move files to the cloud and"
      note "break the Python packages. A folder that isn't synced, like ~/personal-finance, is safer." ;;
  esac
  note "Installing Python and the packages (a minute or two the first time)..."
  (cd "$DIR" && "$UV" sync) >>"$LOG" 2>&1 || { show_log_tail; die "The packages didn't install: see above."; }
  ok "packages installed"
  mkdir -p "$DIR/data"
}

# --------------------------------------------------------- the Desktop app

# Whether $1 is a Personal Finance this installer made.
ours() {
  [ -d "$1" ] && [ "$(plutil -extract CFBundleIdentifier raw -o - "$1/Contents/Info.plist" 2>/dev/null)" = "$APP_ID" ]
}

make_app() {
  dest=$1
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/personal-finance-app.XXXXXX") || return 1
  app="$tmp/$APP_NAME.app"
  cat >"$tmp/main.applescript" <<'EOF'
-- Personal Finance, made by install.sh: opens the page, starting it first if it isn't running.
-- Contents/Resources/open.sh names the copy.
on run
	set opener to (POSIX path of (path to me)) & "Contents/Resources/open.sh"
	try
		do shell script "/bin/sh " & quoted form of opener
	on error errorText number errorNumber
		if errorNumber is not -128 then
			display dialog errorText buttons {"OK"} default button "OK" with title "Personal Finance" with icon caution
		end if
	end try
end run
EOF
  quiet osacompile -o "$app" "$tmp/main.applescript" || return 1
  # The app's own icon (icon.py) in place of the script icon. The asset catalog holds the old one,
  # and would win over applet.icns.
  if (cd "$DIR" && "$UV" run --quiet icon.py --iconset "$tmp/icon.iconset") >>"$LOG" 2>&1 &&
     quiet iconutil -c icns "$tmp/icon.iconset" -o "$app/Contents/Resources/applet.icns"; then
    rm -f "$app/Contents/Resources/Assets.car"
    quiet plutil -remove CFBundleIconName "$app/Contents/Info.plist"
  fi
  quoted=$(printf '%s' "$DIR" | sed "s/'/'\\\\''/g")
  cat >"$app/Contents/Resources/open.sh" <<EOF
#!/bin/sh
# Personal Finance, made by install.sh for the copy below. Run install.sh in that copy again to
# make it anew, or to point it at a copy you moved.
FOLDER='$quoted'
if [ ! -f "\$FOLDER/launch.sh" ]; then
  echo "Personal Finance can't find its folder, \$FOLDER. If you moved or renamed it, open Terminal in its new place and run: sh install.sh. That puts a new Personal Finance on the Desktop." >&2
  exit 1
fi
exec /bin/sh "\$FOLDER/launch.sh"
EOF
  quiet plutil -replace CFBundleIdentifier -string "$APP_ID" "$app/Contents/Info.plist" || return 1
  # Editing the bundle broke osacompile's signature. Seal it again, or Apple silicon may refuse
  # to open it.
  quiet codesign --force --sign - "$app" || return 1
  rm -rf "$dest" && mv "$app" "$dest" || return 1
  rm -rf "$tmp"
}

ensure_app() {
  step "4. Personal Finance on your Desktop"
  note "If macOS asks whether Terminal may use your Desktop folder, click Allow."
  APP="$DESKTOP/$APP_NAME.app"
  if [ -e "$APP" ] && ! ours "$APP"; then
    die "$DESKTOP already has something else called $APP_NAME, which stays as it is. Rename it, then run this again."
  fi
  mkdir -p "$DESKTOP" 2>/dev/null
  make_app "$APP" || { show_log_tail; die "Couldn't make $APP: see above."; }
  ok "$APP"
}

# ------------------------------------------------------------------- the phone

ensure_phone() {
  if [ -z "$PHONE" ]; then
    [ -r /dev/tty ] || return 0
    printf '\n%s' "Set up Personal Finance on your phone too? It needs the free Tailscale app. [y/N] "
    read -r answer </dev/tty || answer=
    case $answer in [yY]*) ;; *) note "Skipped. To set it up later: cd $DIR && sh install.sh --phone"; return 0 ;; esac
  fi
  step "5. Personal Finance on your phone"
  (cd "$DIR" && "$UV" run phone.py enable) || die "Phone access isn't set up: see above. The Desktop app works without it."
}

# --------------------------------------------------------------------- run

step "Personal Finance"
preflight
ensure_git
ensure_uv
ensure_copy
restart_page
ensure_app
ensure_phone

step "Done"
note "Double-click Personal Finance on your Desktop whenever you want the page. It opens in your"
note "browser, with no Terminal window, and stops by itself twenty minutes after its last tab closes."
if [ ! -f "$DIR/data/accounts.toml" ] || [ ! -f "$DIR/data/budget.toml" ]; then
  note "Until you set it up, it shows example accounts and an example budget: see Set it up in"
  note "$DIR/README.md."
fi
if [ -n "$OPEN" ]; then
  /bin/sh "$DIR/launch.sh" || die "The page didn't open: see the message above."
fi

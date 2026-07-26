#!/bin/zsh
set -euo pipefail

REMOTE="mars@minispace.local"
REMOTE_DIR="/Users/mars/homeMonitor"
LABEL="com.mars.home-monitor"

ssh "$REMOTE" "mkdir -p '$REMOTE_DIR' '$REMOTE_DIR/data' '/Users/mars/Library/LaunchAgents'"
rsync -a --delete \
  --exclude '.venv/' \
  --exclude '.pio/' \
  --exclude 'data/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  ./ "$REMOTE:$REMOTE_DIR/"
scp deploy/$LABEL.plist "$REMOTE:/Users/mars/Library/LaunchAgents/$LABEL.plist"

ssh "$REMOTE" <<'REMOTE_SCRIPT'
set -euo pipefail
cd /Users/mars/homeMonitor
/usr/bin/python3 -m pip install --user -r requirements.txt
/bin/launchctl bootout "gui/$(id -u)/com.mars.home-monitor" 2>/dev/null || true
/bin/launchctl bootstrap "gui/$(id -u)" /Users/mars/Library/LaunchAgents/com.mars.home-monitor.plist
/bin/launchctl kickstart -k "gui/$(id -u)/com.mars.home-monitor"
sleep 2
/bin/launchctl print "gui/$(id -u)/com.mars.home-monitor" | sed -n '1,24p'
/usr/bin/curl -fsS http://127.0.0.1:8787/api/health
printf '\n'
/usr/bin/curl -fsS http://127.0.0.1:8787/api/proxies
printf '\n'
REMOTE_SCRIPT

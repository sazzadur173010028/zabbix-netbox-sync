#!/bin/bash
# Usage: ./install.sh [cron-minutes, default 30]
# Installs requests, creates ~/.z2n.env from the template (if missing) and a cron entry.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
MIN="${1:-30}"
python3 -c "import requests" 2>/dev/null || sudo apt-get install -y python3-requests
if [ ! -f "$HOME/.z2n.env" ]; then
  cp "$DIR/.env.example" "$HOME/.z2n.env"; chmod 600 "$HOME/.z2n.env"
  echo "Created ~/.z2n.env. Edit it (nano ~/.z2n.env), then re-run this script."; exit 0
fi
chmod +x "$DIR/z2n_run.sh"
echo "Dry run first (nothing is written):"
( set -a; . "$HOME/.z2n.env"; set +a; DRY_RUN=1 python3 "$DIR/zabbix2netbox.py" | tail -5 )
( crontab -l 2>/dev/null | grep -v 'z2n_run.sh'; echo "*/$MIN * * * * /bin/bash $DIR/z2n_run.sh >> $HOME/z2n.log 2>&1" ) | crontab -
echo "Cron installed (every $MIN min). Log: ~/z2n.log"

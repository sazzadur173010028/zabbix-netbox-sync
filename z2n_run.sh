#!/bin/bash
# Runs both steps; flock prevents two overlapping runs. Used by cron.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec 9>/tmp/z2n.lock
flock -n 9 || { echo "$(date) previous run still active, skip"; exit 0; }
set -a; . "${Z2N_ENV:-$HOME/.z2n.env}"; set +a
echo "=== $(date) ==="
python3 "$DIR/zabbix2netbox.py" && python3 "$DIR/z2n_sync.py"

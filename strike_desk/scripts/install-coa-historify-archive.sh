#!/usr/bin/env bash
# Install the CoA Historify 1m archive timer. Does not restart CoA or OpenAlgo.
set -euo pipefail

SRC=${1:-/tmp/sd-coa-archive}
DEST_SCRIPTS=/opt/strike-desk/current/strike_desk/scripts
DEST_PKG=/opt/strike-desk/current/strike_desk/src/strike_desk

if [[ ! -f $SRC/coa_historify_archive.py ]]; then
  echo "missing coa_historify_archive.py" >&2
  exit 1
fi
if [[ ! -f $SRC/coa_archive.py ]]; then
  echo "missing coa_archive.py" >&2
  exit 1
fi
if [[ ! -f $SRC/coa_boards.py ]]; then
  echo "missing coa_boards.py" >&2
  exit 1
fi
if [[ ! -f $SRC/coa_replay_host.py ]]; then
  echo "missing coa_replay_host.py" >&2
  exit 1
fi

install -d -m 0755 "$DEST_SCRIPTS"
install -d -m 0755 "$DEST_PKG"
install -m 0644 -o strikedesk -g strikedesk "$SRC/coa_archive.py" "$DEST_PKG/coa_archive.py"
install -m 0644 -o strikedesk -g strikedesk "$SRC/coa_boards.py" "$DEST_PKG/coa_boards.py"
install -m 0755 -o strikedesk -g strikedesk "$SRC/coa_historify_archive.py" "$DEST_SCRIPTS/coa_historify_archive.py"
install -m 0755 -o strikedesk -g strikedesk "$SRC/coa_replay_host.py" "$DEST_SCRIPTS/coa_replay_host.py"
install -m 0644 "$SRC/coa-historify-archive.service" /etc/systemd/system/coa-historify-archive.service
install -m 0644 "$SRC/coa-historify-archive.timer" /etc/systemd/system/coa-historify-archive.timer
systemctl daemon-reload
systemctl enable coa-historify-archive.timer
systemctl start coa-historify-archive.timer
systemctl is-enabled coa-historify-archive.timer
systemctl list-timers --all | grep -i coa-historify || true
echo INSTALL_OK

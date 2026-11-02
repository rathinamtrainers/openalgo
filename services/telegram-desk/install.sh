#!/bin/bash
# Install the desk Telegram mirror on the Strike Desk host.
# Does not create the BotFather token. DRY_RUN stays true until the owner flips it.
set -euo pipefail
SRC=${1:-/tmp/zta-telegram-desk}
python3 -c "import requests" 2>/dev/null || apt-get install -y python3-requests
test -f "$SRC/zta_telegram_bot.py"
id zta-desk >/dev/null 2>/dev/null || useradd --system --home /var/lib/zta-desk --shell /usr/sbin/nologin zta-desk
install -d -m 0755 /opt/zta-desk
install -d -m 0755 -o zta-desk -g zta-desk /var/lib/zta-desk /var/lib/zta-desk/ledger /var/lib/zta-desk/public
install -d -m 0755 /var/backups/zta-desk
install -m 0644 "$SRC/zta_telegram_bot.py" /opt/zta-desk/zta_telegram_bot.py
install -m 0644 "$SRC/healthcheck.py" /opt/zta-desk/healthcheck.py
install -m 0644 "$SRC/build_ledger_page.py" /opt/zta-desk/build_ledger_page.py
if [ ! -f /opt/zta-desk/config.env ]; then
  install -m 0600 -o zta-desk -g zta-desk "$SRC/config.example.env" /opt/zta-desk/config.env
else
  chown zta-desk:zta-desk /opt/zta-desk/config.env
  chmod 0600 /opt/zta-desk/config.env
fi
install -m 0644 "$SRC/zta-desk-telegram.service" /etc/systemd/system/zta-desk-telegram.service
install -m 0644 "$SRC/zta-desk-health.service" /etc/systemd/system/zta-desk-health.service
install -m 0644 "$SRC/zta-desk-health.timer" /etc/systemd/system/zta-desk-health.timer
if [ -f "$SRC/zta-desk-ledger.cron" ]; then
  install -m 0755 "$SRC/zta-desk-ledger.cron" /etc/cron.daily/zta-desk-ledger
fi
systemctl daemon-reload
systemctl enable --now zta-desk-telegram.service zta-desk-health.timer
python3 /opt/zta-desk/build_ledger_page.py || true
echo INSTALL_OK

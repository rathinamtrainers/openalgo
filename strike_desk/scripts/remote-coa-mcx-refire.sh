#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/sd-coa-mcx-hands/remote-coa-mcx-refire.py
set -a
# shellcheck disable=SC1091
source /etc/strike-desk/strike-desk.env
if [[ -f /run/strike-desk/env ]]; then
  # shellcheck disable=SC1091
  source /run/strike-desk/env
fi
set +a
cd /opt/strike-desk/current/strike_desk
exec /usr/local/bin/uv run python /tmp/sd-coa-mcx-hands/remote-coa-mcx-refire.py
